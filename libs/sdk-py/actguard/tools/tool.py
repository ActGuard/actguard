import functools
import inspect

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
    """Unified decorator. Each kwarg maps to the corresponding standalone decorator.

    Unspecified guards are not applied. Execution order:
    idempotent → max_attempts → circuit_breaker → rate_limit → timeout → fn.
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
            if emit_all_tool_runs_enabled():
                _emit_tool_invoked(tool_qname)
            try:
                result = await wrapped(*args, **kwargs)
                if emit_all_tool_runs_enabled():
                    _emit_tool_succeeded(tool_qname)
                return result
            except Exception as exc:
                _emit_tool_error(tool_qname, exc)
                raise

        return _async_wrap

    @functools.wraps(fn)
    def _sync_wrap(*args, **kwargs):
        if emit_all_tool_runs_enabled():
            _emit_tool_invoked(tool_qname)
        try:
            result = wrapped(*args, **kwargs)
            if emit_all_tool_runs_enabled():
                _emit_tool_succeeded(tool_qname)
            return result
        except Exception as exc:
            _emit_tool_error(tool_qname, exc)
            raise

    return _sync_wrap


def _emit_tool_invoked(tool_name: str) -> None:
    try:
        from actguard.reporting import emit_event
        emit_event("tool", "invoked", {"tool_name": tool_name})
    except Exception:
        pass


def _emit_tool_succeeded(tool_name: str) -> None:
    try:
        from actguard.reporting import emit_event
        emit_event("tool", "succeeded", {"tool_name": tool_name}, outcome="success")
    except Exception:
        pass


def _emit_tool_error(tool_name: str, exc: Exception) -> None:
    from actguard.exceptions import ActGuardViolation, ToolGuardError

    # Guard decorators emit guard.blocked / guard.intervention directly.
    if isinstance(exc, ToolGuardError):
        return
    # ActGuardViolation has its own emit_violation path.
    if isinstance(exc, ActGuardViolation):
        return

    emit_tool_failure(tool_name, exc)
