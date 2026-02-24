import functools
import inspect

from ..core.run_context import require_run_state


def max_attempts(fn=None, *, calls: int):
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 1:
        raise ValueError(f"max_attempts: calls must be an integer >= 1, got {calls!r}")

    if fn is None:
        return lambda f: max_attempts(f, calls=calls)

    tool_id = f"{fn.__module__}:{fn.__qualname__}"

    def _check_and_increment():
        from ..exceptions import MaxAttemptsExceeded

        state = require_run_state()
        with state._lock:
            state._tool_attempts[tool_id] = state._tool_attempts.get(tool_id, 0) + 1
            used = state._tool_attempts[tool_id]
        if used > calls:
            raise MaxAttemptsExceeded(
                run_id=state.run_id, tool_name=tool_id, limit=calls, used=used
            )

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            _check_and_increment()
            return await fn(*args, **kwargs)

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        _check_and_increment()
        return fn(*args, **kwargs)

    return sync_wrapper
