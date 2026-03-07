"""Tests for the @timeout decorator."""

import asyncio
import contextvars
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import actguard
from actguard.exceptions import (
    ActGuardError,
    ToolExecutionError,
    ToolTimeoutError,
)
from actguard.tools.timeout import shutdown, timeout

_CLIENT = actguard.Client()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MY_VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_MY_VAR", default="unset"
)


# ---------------------------------------------------------------------------
# 1. Sync success
# ---------------------------------------------------------------------------


def test_sync_success():
    @timeout(1.0)
    def fast():
        return "done"

    assert fast() == "done"


# ---------------------------------------------------------------------------
# 2. Async success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_success():
    @timeout(1.0)
    async def fast():
        return "async-done"

    assert await fast() == "async-done"


# ---------------------------------------------------------------------------
# 3. Sync timeout
# ---------------------------------------------------------------------------


def test_sync_timeout_raises():
    @timeout(0.05)
    def slow():
        time.sleep(10)

    with pytest.raises(ToolTimeoutError) as exc_info:
        slow()

    err = exc_info.value
    assert err.tool_name == slow.__qualname__
    assert err.timeout_s == 0.05


# ---------------------------------------------------------------------------
# 4. Async timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_timeout_raises():
    @timeout(0.05)
    async def slow():
        await asyncio.sleep(10)

    with pytest.raises(ToolTimeoutError) as exc_info:
        await slow()

    err = exc_info.value
    assert err.timeout_s == 0.05


# ---------------------------------------------------------------------------
# 5. Queue time counts toward timeout (1-worker executor)
# ---------------------------------------------------------------------------


def test_queue_time_counts_toward_timeout():
    single_worker = ThreadPoolExecutor(max_workers=1)

    @timeout(0.1, executor=single_worker)
    def occupier():
        time.sleep(10)

    @timeout(0.1, executor=single_worker)
    def waiter():
        return "ok"

    # Submit occupier in background so it holds the sole worker
    fut = single_worker.submit(time.sleep, 10)
    try:
        with pytest.raises(ToolTimeoutError):
            waiter()
    finally:
        fut.cancel()
        single_worker.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 6. Context propagation
# ---------------------------------------------------------------------------


def test_context_propagation_to_sync_tool():
    @timeout(1.0)
    def read_var():
        return _MY_VAR.get()

    token = _MY_VAR.set("hello")
    try:
        result = read_var()
    finally:
        _MY_VAR.reset(token)

    assert result == "hello"


# ---------------------------------------------------------------------------
# 7. Generator pre-check at decoration
# ---------------------------------------------------------------------------


def test_generator_function_rejected_at_decoration():
    with pytest.raises(TypeError, match="generator"):

        @timeout(1.0)
        def gen():
            yield 1


# ---------------------------------------------------------------------------
# 8. Generator post-check (function returns generator at runtime)
# ---------------------------------------------------------------------------


def test_generator_returned_at_runtime_raises_type_error():
    def _inner():
        yield 1

    def returns_gen():
        return _inner()

    guarded = timeout(1.0)(returns_gen)

    with pytest.raises(TypeError, match="generator"):
        guarded()


# ---------------------------------------------------------------------------
# 9. Async generator pre-check at decoration
# ---------------------------------------------------------------------------


def test_async_generator_rejected_at_decoration():
    with pytest.raises(TypeError, match="generator"):

        @timeout(1.0)
        async def agen():
            yield 1


# ---------------------------------------------------------------------------
# 10. run_id in exception inside client.run(...)
# ---------------------------------------------------------------------------


def test_run_id_in_exception_inside_client_run():
    @timeout(0.05)
    def slow():
        time.sleep(10)

    with _CLIENT.run(run_id="my-run-123"):
        with pytest.raises(ToolTimeoutError) as exc_info:
            slow()

    assert exc_info.value.run_id == "my-run-123"


# ---------------------------------------------------------------------------
# 11. functools.wraps preservation
# ---------------------------------------------------------------------------


def test_functools_wraps_preserves_metadata():
    @timeout(1.0)
    def my_special_tool():
        """My docstring."""
        pass

    assert my_special_tool.__name__ == "my_special_tool"
    assert "my_special_tool" in my_special_tool.__qualname__
    assert my_special_tool.__doc__ == "My docstring."


@pytest.mark.asyncio
async def test_functools_wraps_preserves_metadata_async():
    @timeout(1.0)
    async def my_async_tool():
        """Async docstring."""
        pass

    assert my_async_tool.__name__ == "my_async_tool"
    assert my_async_tool.__doc__ == "Async docstring."


# ---------------------------------------------------------------------------
# 12. Custom executor is used
# ---------------------------------------------------------------------------


def test_custom_executor_is_used():
    calls = []

    class TrackingExecutor(ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            calls.append("submitted")
            return super().submit(fn, *args, **kwargs)

    custom_exec = TrackingExecutor(max_workers=2)

    @timeout(1.0, executor=custom_exec)
    def fn():
        return "ok"

    result = fn()
    custom_exec.shutdown(wait=True)

    assert result == "ok"
    assert calls == ["submitted"]


# ---------------------------------------------------------------------------
# 13. shutdown() resets module executor; re-use auto-recreates
# ---------------------------------------------------------------------------


def test_shutdown_resets_and_recreates_executor():
    # sys.modules avoids name clash: actguard.tools.__dict__['timeout'] is the function
    _mod = sys.modules["actguard.tools.timeout"]

    # Ensure a module executor exists
    @timeout(1.0)
    def fn():
        return "ok"

    fn()  # triggers lazy init

    shutdown(wait=True)
    assert _mod._executor is None

    # After shutdown, a new call should auto-recreate
    fn()
    assert _mod._executor is not None

    # Cleanup
    shutdown(wait=True)


# ---------------------------------------------------------------------------
# 14. Unified @tool decorator
# ---------------------------------------------------------------------------


def test_tool_decorator_timeout_kwarg():
    @actguard.tool(timeout=0.05)
    def slow():
        time.sleep(10)

    with pytest.raises(ToolTimeoutError):
        slow()


@pytest.mark.asyncio
async def test_tool_decorator_timeout_async():
    @actguard.tool(timeout=0.05)
    async def slow():
        await asyncio.sleep(10)

    with pytest.raises(ToolTimeoutError):
        await slow()


def test_tool_decorator_timeout_success():
    @actguard.tool(timeout=1.0)
    def fast():
        return "ok"

    assert fast() == "ok"


# ---------------------------------------------------------------------------
# 15. Exception hierarchy
# ---------------------------------------------------------------------------


def test_exception_hierarchy():
    err = ToolTimeoutError("my_tool", 5.0)
    assert isinstance(err, ToolTimeoutError)
    assert isinstance(err, ToolExecutionError)
    assert isinstance(err, ActGuardError)


def test_tool_timeout_error_fields():
    err = ToolTimeoutError("some_tool", 2.5, run_id="run-999")
    assert err.tool_name == "some_tool"
    assert err.timeout_s == 2.5
    assert err.run_id == "run-999"
    assert "TOOL_TIMEOUT" in str(err)
    assert "some_tool" in str(err)
    assert "2.5" in str(err)


def test_tool_timeout_error_no_run_id():
    err = ToolTimeoutError("t", 1.0)
    assert err.run_id is None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def test_public_exports():
    assert hasattr(actguard, "timeout")
    assert hasattr(actguard, "shutdown")
    assert hasattr(actguard, "ToolTimeoutError")
    assert hasattr(actguard, "ToolExecutionError")
    assert hasattr(actguard, "ActGuardError")


def test_public_all_contains_new_symbols():
    assert "timeout" in actguard.__all__
    assert "shutdown" in actguard.__all__
    assert "ToolTimeoutError" in actguard.__all__
    assert "ToolExecutionError" in actguard.__all__
    assert "ActGuardError" in actguard.__all__
