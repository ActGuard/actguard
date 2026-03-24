import functools
import inspect
import time
from typing import Optional

from ..exceptions import RateLimitExceeded
from ._cache import get_cache
from ._observability import emit_guard_blocked
from ._scope import extract_arg, validate_scope


def rate_limit(
    fn=None,
    *,
    max_calls: int = 10,
    period: float = 60.0,
    scope: Optional[str] = None,
):
    """Apply a sliding-window call limit to a tool.

    Use this when a tool should only be callable a certain number of times over
    a time window, either globally or partitioned by a function argument such as
    ``user_id``.

    Args:
        max_calls: Maximum number of calls allowed within the period.
        period: Time window in seconds.
        scope: Name of a function parameter to partition rate limits by.
               If None, a single global counter is used for all callers.
    """
    if fn is None:
        return lambda f: rate_limit(f, max_calls=max_calls, period=period, scope=scope)

    if scope is not None:
        validate_scope(fn, scope)

    is_async = inspect.iscoroutinefunction(fn)

    def _do_check(args, kwargs):
        if scope is not None:
            scope_val = str(extract_arg(fn, scope, args, kwargs))
        else:
            scope_val = "__global__"

        key = f"ratelimit:{fn.__qualname__}:{scope_val}"
        cache = get_cache()

        with cache.transact():
            now = time.time()
            cutoff = now - period
            timestamps = [t for t in cache.get(key, []) if t > cutoff]

            if len(timestamps) >= max_calls:
                retry_after = timestamps[0] + period - now
                error = RateLimitExceeded(
                    func_name=fn.__qualname__,
                    scope_value=scope_val,
                    max_calls=max_calls,
                    period=period,
                    retry_after=retry_after,
                )
                emit_guard_blocked(
                    fn.__qualname__,
                    "rate_limit",
                    error,
                    extra={
                        "max_calls": max_calls,
                        "period": period,
                        "scope_value": scope_val,
                        "retry_after": retry_after,
                    },
                )
                raise error

            timestamps.append(now)
            cache.set(key, timestamps)

    if is_async:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            _do_check(args, kwargs)
            return await fn(*args, **kwargs)
    else:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            _do_check(args, kwargs)
            return fn(*args, **kwargs)

    return wrapper
