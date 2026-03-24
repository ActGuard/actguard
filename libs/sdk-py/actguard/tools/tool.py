import functools
import inspect

from actguard._monitoring import warn_monitoring_issue
from actguard.events.context import reset_tool_name, set_tool_name

from ._observability import emit_all_tool_runs_enabled, emit_tool_failure


def tool(
    fn=None,
    *,
    rate_limit=None,
    circuit_breaker=None,
    max_attempts=None,
    timeout=None,
    timeout_executor=None,
    idempotent=None,
    policy=None,
):
    """Apply multiple runtime tool guardrails with one decorator.

    Use this when a single tool needs more than one of the standard runtime
    protections, such as rate limiting, circuit breaking, max-attempts,
    idempotency, or timeouts.

    Each kwarg maps to the corresponding standalone decorator. Unspecified
    guards are not applied.

    ``max_attempts`` and ``idempotent`` require an active ``client.run(...)``
    scope. ``prove`` and ``enforce`` are not composed here; keep those as
    separate decorators because they depend on ``actguard.session(...)``.

    Execution order:
    ``idempotent -> max_attempts -> circuit_breaker -> rate_limit -> timeout -> fn``

    Example:
        >>> @actguard.tool(
        ...     idempotent={"ttl_s": 600},
        ...     max_attempts={"calls": 3},
        ...     rate_limit={"max_calls": 10, "period": 60, "scope": "user_id"},
        ...     timeout=2.0,
        ... )
        ... def search_web(user_id: str, query: str, *, idempotency_key: str) -> str:
        ...     ...
    """
    if fn is None:
        return lambda f: tool(
            f,
            rate_limit=rate_limit,
            circuit_breaker=circuit_breaker,
            max_attempts=max_attempts,
            timeout=timeout,
            timeout_executor=timeout_executor,
            idempotent=idempotent,
            policy=policy,
        )

    wrapped = fn

    if timeout is not None:
        from .timeout import timeout as _to

        wrapped = _to(timeout, executor=timeout_executor)(wrapped)

    if circuit_breaker is not None:
        from .circuit_breaker import circuit_breaker as _cb

        wrapped = _cb(wrapped, **circuit_breaker)

    if rate_limit is not None:
        from .rate_limit import rate_limit as _rl

        wrapped = _rl(wrapped, **rate_limit)

    if max_attempts is not None:
        from .max_attempts import max_attempts as _ma

        wrapped = _ma(wrapped, **max_attempts)

    if idempotent is not None:
        from .idempotent import idempotent as _idem

        wrapped = _idem(wrapped, **idempotent)

    # policy: stub reserved for future phases

    tool_qname = fn.__qualname__

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _async_wrap(*args, **kwargs):
            tool_token = set_tool_name(tool_qname)
            try:
                result = await wrapped(*args, **kwargs)
                if emit_all_tool_runs_enabled():
                    _emit_tool_invoke(tool_qname)
                return result
            except Exception as exc:
                _emit_tool_error(tool_qname, exc)
                raise
            finally:
                reset_tool_name(tool_token)

        return _async_wrap

    @functools.wraps(fn)
    def _sync_wrap(*args, **kwargs):
        tool_token = set_tool_name(tool_qname)
        try:
            result = wrapped(*args, **kwargs)
            if emit_all_tool_runs_enabled():
                _emit_tool_invoke(tool_qname)
            return result
        except Exception as exc:
            _emit_tool_error(tool_qname, exc)
            raise
        finally:
            reset_tool_name(tool_token)

    return _sync_wrap


def _emit_tool_invoke(tool_name: str) -> None:
    try:
        from actguard.reporting import emit_event
        emit_event("tool", "invoke", {"tool_name": tool_name}, outcome="success")
    except Exception as exc:
        warn_monitoring_issue(
            subsystem="reporting",
            operation="tool.invoke",
            exc=exc,
            stacklevel=2,
        )


def _emit_tool_error(tool_name: str, exc: Exception) -> None:
    from actguard.exceptions import ActGuardError, ActGuardToolError

    # Guard decorators emit guard.blocked / guard.intervention directly.
    if isinstance(exc, ActGuardToolError):
        return
    # Reportable ActGuard errors have their own emit_violation path.
    if isinstance(exc, ActGuardError) and exc.is_reportable:
        return

    emit_tool_failure(tool_name, exc)
