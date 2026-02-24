import functools
import inspect
import time
from datetime import datetime, timezone
from typing import Optional

from .._gateway import report_event
from ..exceptions import RateLimitExceeded
from ._cache import get_cache
from ._scope import extract_arg, validate_scope


def rate_limit(
    fn=None,
    *,
    max_calls: int = 10,
    period: float = 60.0,
    scope: Optional[str] = None,
):
    """Sliding-window rate limit decorator (sync + async).

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
                _report(fn, scope_val, allowed=False, retry_after=retry_after)
                raise RateLimitExceeded(
                    func_name=fn.__qualname__,
                    scope_value=scope_val,
                    max_calls=max_calls,
                    period=period,
                    retry_after=retry_after,
                )

            timestamps.append(now)
            cache.set(key, timestamps)

        _report(fn, scope_val, allowed=True)

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


def _report(
    fn, scope_val: str, *, allowed: bool, retry_after: Optional[float] = None
) -> None:
    event = {
        "type": "rate_limit_check" if allowed else "rate_limit_exceeded",
        "func": fn.__qualname__,
        "scope_value": scope_val,
        "allowed": allowed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if retry_after is not None:
        event["retry_after"] = retry_after
    report_event(event)
