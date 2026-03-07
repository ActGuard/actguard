from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import functools
import inspect
import logging
import os
import threading
from concurrent.futures import Executor, ThreadPoolExecutor

from ..exceptions import ToolTimeoutError
from ._observability import emit_guard_intervention

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level shared executor (lazy-init, thread-safe)
# ---------------------------------------------------------------------------

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor(custom: Executor | None) -> Executor:
    if custom is not None:
        return custom
    global _executor
    with _executor_lock:
        if _executor is None or _executor._shutdown:
            _executor = ThreadPoolExecutor(
                max_workers=min(32, (os.cpu_count() or 1) + 4)
            )
    return _executor


def shutdown(wait: bool = True) -> None:
    """Shutdown the shared executor. Call in tests or process teardown."""
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=wait)
            _executor = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_run_id() -> str | None:
    from ..core.run_context import get_run_state

    state = get_run_state()
    if state is None:
        return None
    return state.run_id


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def timeout(seconds: float, executor: Executor | None = None):
    """Bound the wall-clock duration of a tool invocation.

    Raises ToolTimeoutError if the tool does not complete within *seconds*.
    Supports both sync and async functions. Generator functions are rejected
    at decoration time.

    Args:
        seconds: Maximum allowed wall-clock time in seconds.
        executor: Optional custom Executor for sync functions. Defaults to a
                  module-level ThreadPoolExecutor.
    """

    def decorator(fn):
        if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
            raise TypeError(
                f"@timeout does not support generator functions: {fn.__qualname__}"
            )

        tool_name = fn.__qualname__

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await asyncio.wait_for(fn(*args, **kwargs), timeout=seconds)
                except asyncio.TimeoutError:
                    run_id = _get_run_id()
                    log.warning(
                        "actguard timeout async_cancelled tool=%s"
                        " timeout_s=%s run_id=%s",
                        tool_name,
                        seconds,
                        run_id,
                    )
                    emit_guard_intervention(
                        tool_name,
                        "timeout",
                        extra={"timeout_s": seconds},
                    )
                    raise ToolTimeoutError(tool_name, seconds, run_id=run_id)

            return async_wrapper

        else:

            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                ctx = contextvars.copy_context()
                future = _get_executor(executor).submit(
                    ctx.run, functools.partial(fn, *args, **kwargs)
                )
                try:
                    result = future.result(timeout=seconds)
                    if inspect.isgenerator(result):
                        raise TypeError(
                            f"@timeout: tool '{tool_name}' returned a generator;"
                            " streaming not supported"
                        )
                    return result
                except concurrent.futures.TimeoutError:
                    run_id = _get_run_id()
                    log.warning(
                        "actguard timeout sync_zombie_detached tool=%s"
                        " timeout_s=%s run_id=%s",
                        tool_name,
                        seconds,
                        run_id,
                    )
                    emit_guard_intervention(
                        tool_name,
                        "timeout",
                        extra={"timeout_s": seconds},
                    )
                    raise ToolTimeoutError(tool_name, seconds, run_id=run_id)

            return sync_wrapper

    return decorator
