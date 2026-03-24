import errno
import functools
import inspect
import threading
import time
from enum import Enum
from typing import Any, Optional

from ..exceptions import CircuitBreakerConfigurationError, CircuitOpenError
from ._observability import emit_guard_blocked


class FailureKind(str, Enum):
    """Stable failure taxonomy for circuit breaker behavior."""

    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    OVERLOADED = "overloaded"
    THROTTLED = "throttled"
    AUTH = "auth"
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


FAIL_ON_DEFAULT = frozenset(
    {FailureKind.TRANSPORT, FailureKind.TIMEOUT, FailureKind.OVERLOADED}
)
IGNORE_ON_DEFAULT = frozenset(
    {FailureKind.INVALID, FailureKind.NOT_FOUND, FailureKind.CONFLICT}
)
FAIL_ON_STRICT = FAIL_ON_DEFAULT | frozenset({FailureKind.AUTH, FailureKind.THROTTLED})
FAIL_ON_INFRA_ONLY = frozenset({FailureKind.TRANSPORT, FailureKind.TIMEOUT})

if FAIL_ON_DEFAULT & IGNORE_ON_DEFAULT:
    raise CircuitBreakerConfigurationError(
        "FAIL_ON_DEFAULT and IGNORE_ON_DEFAULT must be disjoint"
    )

_TRANSPORT_ERRNOS = {
    errno.ECONNRESET,
    errno.ECONNREFUSED,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
    errno.ETIMEDOUT,
}
for _name in ("EAI_NONAME", "EAI_AGAIN"):
    _value = getattr(errno, _name, None)
    if _value is not None:
        _TRANSPORT_ERRNOS.add(_value)


class _CircuitState:
    def __init__(self) -> None:
        self.is_open = False
        self.failure_count = 0
        self.opened_at: Optional[float] = None
        self.lock = threading.RLock()


def circuit_breaker(
    fn=None,
    *,
    name: str,
    max_fails: int = 3,
    reset_timeout: float = 60.0,
    fail_on=FAIL_ON_DEFAULT,
    ignore_on=IGNORE_ON_DEFAULT,
):
    """Short-circuit calls after repeated failures from one dependency.

    Use this around tools that call an external service which can become
    unhealthy. Once enough counted failures occur, the circuit opens and future
    calls fail fast until ``reset_timeout`` elapses.

    Args:
        name: Stable dependency name used in errors and observability.
        max_fails: Number of counted failures before the circuit opens.
        reset_timeout: Seconds to wait before allowing another call.
        fail_on: Failure kinds that count toward opening the circuit.
        ignore_on: Failure kinds that never count toward opening the circuit.
    """
    if fn is None:
        return lambda f: circuit_breaker(
            f,
            name=name,
            max_fails=max_fails,
            reset_timeout=reset_timeout,
            fail_on=fail_on,
            ignore_on=ignore_on,
        )

    _validate_name(name)
    _validate_thresholds(max_fails, reset_timeout)
    fail_on_set = _validate_kind_set("fail_on", fail_on)
    ignore_on_set = _validate_kind_set("ignore_on", ignore_on)

    overlap = fail_on_set & ignore_on_set
    if overlap:
        raise CircuitBreakerConfigurationError(
            "fail_on and ignore_on must be disjoint; overlapping values: "
            f"{sorted(k.name for k in overlap)}"
        )

    state = _CircuitState()
    is_async = inspect.iscoroutinefunction(fn)

    if is_async:

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                _precheck_open(state, name=name, reset_timeout=reset_timeout)
            except CircuitOpenError as exc:
                emit_guard_blocked(
                    fn.__qualname__,
                    "circuit_breaker",
                    exc,
                    extra={"dependency_name": name, "reset_at": exc.reset_at},
                )
                raise

            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                kind = _classify(result=None, exc=exc)
                _apply_outcome(
                    state,
                    kind=kind,
                    fail_on=fail_on_set,
                    ignore_on=ignore_on_set,
                    max_fails=max_fails,
                )
                raise

            kind = _classify(result=result, exc=None)
            _apply_outcome(
                state,
                kind=kind,
                fail_on=fail_on_set,
                ignore_on=ignore_on_set,
                max_fails=max_fails,
            )
            return result

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        try:
            _precheck_open(state, name=name, reset_timeout=reset_timeout)
        except CircuitOpenError as exc:
            emit_guard_blocked(
                fn.__qualname__,
                "circuit_breaker",
                exc,
                extra={"dependency_name": name, "reset_at": exc.reset_at},
            )
            raise

        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            kind = _classify(result=None, exc=exc)
            _apply_outcome(
                state,
                kind=kind,
                fail_on=fail_on_set,
                ignore_on=ignore_on_set,
                max_fails=max_fails,
            )
            raise

        kind = _classify(result=result, exc=None)
        _apply_outcome(
            state,
            kind=kind,
            fail_on=fail_on_set,
            ignore_on=ignore_on_set,
            max_fails=max_fails,
        )
        return result

    return sync_wrapper


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise CircuitBreakerConfigurationError("name must be a non-empty string")


def _validate_thresholds(max_fails: int, reset_timeout: float) -> None:
    if not isinstance(max_fails, int) or isinstance(max_fails, bool) or max_fails < 1:
        raise CircuitBreakerConfigurationError("max_fails must be an integer >= 1")
    if not isinstance(reset_timeout, (int, float)) or reset_timeout <= 0:
        raise CircuitBreakerConfigurationError("reset_timeout must be > 0")


def _validate_kind_set(field_name: str, kinds) -> frozenset[FailureKind]:
    if not isinstance(kinds, (set, frozenset)):
        raise CircuitBreakerConfigurationError(
            f"{field_name} must be a set of FailureKind"
        )

    normalized: set[FailureKind] = set()
    for item in kinds:
        if not isinstance(item, FailureKind):
            raise CircuitBreakerConfigurationError(
                f"{field_name} must contain only FailureKind values"
            )
        normalized.add(item)

    return frozenset(normalized)


def _precheck_open(state: _CircuitState, *, name: str, reset_timeout: float) -> None:
    now = time.time()

    with state.lock:
        if not state.is_open:
            return

        if state.opened_at is None:
            state.opened_at = now

        reset_at = state.opened_at + reset_timeout
        if now < reset_at:
            raise CircuitOpenError(dependency_name=name, reset_at=reset_at)

        # v0.1 deterministic reset: close before permitting the next call.
        state.is_open = False
        state.failure_count = 0
        state.opened_at = None


def _apply_outcome(
    state: _CircuitState,
    *,
    kind: Optional[FailureKind],
    fail_on: frozenset[FailureKind],
    ignore_on: frozenset[FailureKind],
    max_fails: int,
) -> None:
    with state.lock:
        if kind in ignore_on:
            return

        if kind in fail_on:
            state.failure_count += 1
            if state.failure_count >= max_fails:
                state.is_open = True
                state.opened_at = time.time()
            return

        state.is_open = False
        state.failure_count = 0
        state.opened_at = None


def _classify(*, result: Any, exc: Optional[Exception]) -> Optional[FailureKind]:
    status = _extract_http_status_from_result(result)
    if status is not None:
        mapped = _map_http_status(status)
        if mapped is not None:
            return mapped

    if exc is None:
        return None

    status = _extract_http_status_from_exception(exc)
    if status is not None:
        mapped = _map_http_status(status)
        if mapped is not None:
            return mapped

    grpc_kind = _map_grpc_code(exc)
    if grpc_kind is not None:
        return grpc_kind

    cloud_kind = _map_cloud_response(exc)
    if cloud_kind is not None:
        return cloud_kind

    generic_kind = _map_generic_exception(exc)
    if generic_kind is not None:
        return generic_kind

    db_kind = _map_db_patterns(exc)
    if db_kind is not None:
        return db_kind

    return FailureKind.UNKNOWN


def _extract_http_status_from_result(result: Any) -> Optional[int]:
    if result is None:
        return None

    status = getattr(result, "status_code", None)
    if isinstance(status, int):
        return status

    return None


def _extract_http_status_from_exception(exc: Exception) -> Optional[int]:
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct

    response = getattr(exc, "response", None)
    if response is None:
        return None

    if isinstance(response, dict):
        meta = response.get("ResponseMetadata")
        if isinstance(meta, dict) and isinstance(meta.get("HTTPStatusCode"), int):
            return meta["HTTPStatusCode"]
        if isinstance(response.get("status_code"), int):
            return response["status_code"]
        return None

    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status

    return None


def _map_http_status(status: int) -> Optional[FailureKind]:
    if status in (401, 403):
        return FailureKind.AUTH
    if status == 404:
        return FailureKind.NOT_FOUND
    if status == 409:
        return FailureKind.CONFLICT
    if status == 429:
        return FailureKind.THROTTLED
    if 500 <= status <= 599:
        return FailureKind.OVERLOADED
    if 400 <= status <= 499:
        return FailureKind.INVALID
    return None


def _map_grpc_code(exc: Exception) -> Optional[FailureKind]:
    code_fn = getattr(exc, "code", None)
    if not callable(code_fn):
        return None

    try:
        code = code_fn()
    except Exception:
        return None

    code_name = _normalize_code_name(code)
    if code_name is None:
        return None

    if code_name == "UNAVAILABLE":
        return FailureKind.TRANSPORT
    if code_name == "DEADLINE_EXCEEDED":
        return FailureKind.TIMEOUT
    if code_name == "RESOURCE_EXHAUSTED":
        return FailureKind.OVERLOADED
    if code_name in {"PERMISSION_DENIED", "UNAUTHENTICATED"}:
        return FailureKind.AUTH
    if code_name == "INVALID_ARGUMENT":
        return FailureKind.INVALID
    if code_name == "NOT_FOUND":
        return FailureKind.NOT_FOUND

    return None


def _normalize_code_name(code: Any) -> Optional[str]:
    if code is None:
        return None

    name = getattr(code, "name", None)
    if isinstance(name, str):
        return name.upper()

    if isinstance(code, str):
        value = code
    else:
        value = str(code)

    value = value.upper()
    if "." in value:
        value = value.split(".")[-1]

    if value:
        return value

    return None


def _map_cloud_response(exc: Exception) -> Optional[FailureKind]:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None

    error = response.get("Error")
    error_code = ""
    if isinstance(error, dict):
        code = error.get("Code")
        if isinstance(code, str):
            error_code = code.lower()

    if error_code:
        if "throttl" in error_code or "toomanyrequests" in error_code:
            return FailureKind.THROTTLED
        if any(
            token in error_code
            for token in (
                "accessdenied",
                "unauthorized",
                "unrecognizedclient",
                "invalidtoken",
            )
        ):
            return FailureKind.AUTH
        if "requesttimeout" in error_code:
            return FailureKind.TIMEOUT

    meta = response.get("ResponseMetadata")
    if isinstance(meta, dict):
        status = meta.get("HTTPStatusCode")
        if isinstance(status, int) and 500 <= status <= 599:
            return FailureKind.OVERLOADED

    return None


def _map_generic_exception(exc: Exception) -> Optional[FailureKind]:
    if isinstance(exc, TimeoutError):
        return FailureKind.TIMEOUT

    if isinstance(exc, ConnectionError):
        return FailureKind.TRANSPORT

    if isinstance(exc, OSError):
        err = getattr(exc, "errno", None)
        if err in _TRANSPORT_ERRNOS:
            return FailureKind.TRANSPORT

    return None


def _map_db_patterns(exc: Exception) -> Optional[FailureKind]:
    for code in _iter_possible_sqlstate(exc):
        normalized = code.upper()
        if normalized in {"40P01", "40001"}:
            return FailureKind.CONFLICT
        if normalized in {"42601", "42P01", "42703"}:
            return FailureKind.INVALID
        if normalized == "53300":
            return FailureKind.OVERLOADED

    message = str(exc).lower()

    if "deadlock" in message or "could not serialize access" in message:
        return FailureKind.CONFLICT

    if "too many connections" in message or "remaining connection slots" in message:
        return FailureKind.OVERLOADED

    if (
        "syntax error" in message
        or "undefined table" in message
        or "undefined column" in message
    ):
        return FailureKind.INVALID

    return None


def _iter_possible_sqlstate(exc: Exception):
    candidates = [
        getattr(exc, "sqlstate", None),
        getattr(exc, "pgcode", None),
    ]

    orig = getattr(exc, "orig", None)
    if orig is not None:
        candidates.extend(
            [getattr(orig, "sqlstate", None), getattr(orig, "pgcode", None)]
        )

    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            yield candidate
