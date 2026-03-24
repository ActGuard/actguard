import asyncio
import functools
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from ._observability import emit_guard_blocked, emit_guard_intervention


@dataclass
class IdemEntry:
    state: Literal["IN_PROGRESS", "DONE", "UNKNOWN"]
    started_at: float
    expires_at: float
    result: Any = None
    last_error_type: Optional[type] = field(default=None)


def _check_and_acquire(state, tool_id: str, key: str, ttl_s: float):
    """Called under state._idem_lock. Returns 'ACQUIRED' or ('DONE', result)."""
    from actguard.exceptions import (
        IdempotencyInProgress,
        IdempotencyOutcomeUnknown,
    )

    now = time.monotonic()
    entry: Optional[IdemEntry] = state._idem_store.get((tool_id, key))

    # Lazy expiry
    if entry is not None and now > entry.expires_at:
        del state._idem_store[(tool_id, key)]
        entry = None

    if entry is None:
        state._idem_store[(tool_id, key)] = IdemEntry(
            state="IN_PROGRESS",
            started_at=now,
            expires_at=now + ttl_s,
        )
        return "ACQUIRED"

    if entry.state == "IN_PROGRESS":
        raise IdempotencyInProgress(tool_id, key)

    if entry.state == "DONE":
        return ("DONE", entry.result)

    # UNKNOWN
    raise IdempotencyOutcomeUnknown(tool_id, key, entry.last_error_type)


def _finalize_success(state, tool_id: str, key: str, result: Any, ttl_s: float) -> None:
    """Called under state._idem_lock."""
    now = time.monotonic()
    entry = state._idem_store.get((tool_id, key))
    if entry is not None:
        entry.state = "DONE"
        entry.result = result
        entry.expires_at = now + ttl_s


def _finalize_failure(
    state, tool_id: str, key: str, exc: BaseException, safe_exceptions: tuple
) -> None:
    """Called under state._idem_lock."""
    if safe_exceptions and isinstance(exc, safe_exceptions):
        state._idem_store.pop((tool_id, key), None)
    else:
        entry = state._idem_store.get((tool_id, key))
        if entry is not None:
            entry.state = "UNKNOWN"
            entry.last_error_type = type(exc)


def idempotent(
    fn=None,
    *,
    ttl_s: float = 3600,
    on_duplicate: Literal["return", "raise"] = "return",
    safe_exceptions: tuple = (),
):
    """Prevent duplicate execution for the same ``idempotency_key`` in a run.

    Use this for side-effecting tools such as payments, writes, or outbound API
    calls where the same logical request must not execute twice.

    The decorated function must declare ``idempotency_key``, callers must pass
    it, and the tool must run inside ``client.run(...)``.

    Args:
        ttl_s: How long to remember completed or in-progress keys.
        on_duplicate: ``"return"`` returns the cached result for a completed
            key, while ``"raise"`` raises ``DuplicateIdempotencyKey``.
        safe_exceptions: Exception types that should release the key so the
            caller can retry instead of marking the outcome as unknown.
    """
    if fn is None:
        return lambda f: idempotent(
            f,
            ttl_s=ttl_s,
            on_duplicate=on_duplicate,
            safe_exceptions=safe_exceptions,
        )

    from actguard.exceptions import InvalidIdempotentToolError

    sig = inspect.signature(fn)
    if "idempotency_key" not in sig.parameters:
        raise InvalidIdempotentToolError(
            f"@idempotent requires '{fn.__qualname__}' to have"
            " an 'idempotency_key' parameter."
        )

    tool_id = f"{fn.__module__}:{fn.__qualname__}"

    if asyncio.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            from actguard.core.run_context import require_run_state
            from actguard.exceptions import (
                DuplicateIdempotencyKey,
                MissingIdempotencyKeyError,
            )

            state = require_run_state()
            key = kwargs.get("idempotency_key")
            if not key:
                error = MissingIdempotencyKeyError(tool_id)
                emit_guard_blocked(tool_id, "idempotent", error)
                raise error

            try:
                with state._idem_lock:
                    outcome = _check_and_acquire(state, tool_id, key, ttl_s)
            except Exception as exc:
                emit_guard_blocked(tool_id, "idempotent", exc)
                raise

            if outcome != "ACQUIRED":
                # outcome is ("DONE", result)
                if on_duplicate == "raise":
                    error = DuplicateIdempotencyKey(tool_id, key)
                    emit_guard_blocked(tool_id, "idempotent", error)
                    raise error
                emit_guard_intervention(
                    tool_id,
                    "idempotent",
                    extra={"action": "return_cached"},
                )
                return outcome[1]

            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                with state._idem_lock:
                    _finalize_failure(state, tool_id, key, exc, safe_exceptions)
                raise

            with state._idem_lock:
                _finalize_success(state, tool_id, key, result, ttl_s)
            return result

        return async_wrapper

    else:

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            from actguard.core.run_context import require_run_state
            from actguard.exceptions import (
                DuplicateIdempotencyKey,
                MissingIdempotencyKeyError,
            )

            state = require_run_state()
            key = kwargs.get("idempotency_key")
            if not key:
                error = MissingIdempotencyKeyError(tool_id)
                emit_guard_blocked(tool_id, "idempotent", error)
                raise error

            try:
                with state._idem_lock:
                    outcome = _check_and_acquire(state, tool_id, key, ttl_s)
            except Exception as exc:
                emit_guard_blocked(tool_id, "idempotent", exc)
                raise

            if outcome != "ACQUIRED":
                # outcome is ("DONE", result)
                if on_duplicate == "raise":
                    error = DuplicateIdempotencyKey(tool_id, key)
                    emit_guard_blocked(tool_id, "idempotent", error)
                    raise error
                emit_guard_intervention(
                    tool_id,
                    "idempotent",
                    extra={"action": "return_cached"},
                )
                return outcome[1]

            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                with state._idem_lock:
                    _finalize_failure(state, tool_id, key, exc, safe_exceptions)
                raise

            with state._idem_lock:
                _finalize_success(state, tool_id, key, result, ttl_s)
            return result

        return sync_wrapper
