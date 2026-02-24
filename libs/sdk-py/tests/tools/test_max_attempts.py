"""Tests for the @max_attempts decorator."""

import threading

import pytest

import actguard
from actguard.exceptions import MaxAttemptsExceeded, MissingRuntimeContextError
from actguard.run_context import RunContext
from actguard.tools.max_attempts import max_attempts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fn(calls):
    @max_attempts(calls=calls)
    def fn():
        return "ok"

    return fn


# ---------------------------------------------------------------------------
# Functional — allow / block
# ---------------------------------------------------------------------------


def test_allows_calls_up_to_limit():
    fn = _make_fn(3)
    with RunContext():
        assert fn() == "ok"
        assert fn() == "ok"
        assert fn() == "ok"


def test_raises_on_exceeding_limit():
    fn = _make_fn(2)
    with RunContext():
        fn()
        fn()
        with pytest.raises(MaxAttemptsExceeded):
            fn()


def test_exactly_one_call_allowed():
    fn = _make_fn(1)
    with RunContext():
        assert fn() == "ok"
        with pytest.raises(MaxAttemptsExceeded):
            fn()


def test_exception_fields_match_spec():
    fn = _make_fn(2)
    with RunContext(run_id="test-run-42"):
        fn()
        fn()
        with pytest.raises(MaxAttemptsExceeded) as exc_info:
            fn()

    err = exc_info.value
    assert err.limit == 2
    assert err.used == 3
    assert err.run_id == "test-run-42"
    assert "test_max_attempts" in err.tool_name


def test_exception_message_format():
    fn = _make_fn(1)
    with RunContext(run_id="run-abc"):
        fn()
        with pytest.raises(MaxAttemptsExceeded) as exc_info:
            fn()

    msg = str(exc_info.value)
    assert "MAX_ATTEMPTS_EXCEEDED" in msg
    assert "run-abc" in msg
    assert "1" in msg  # limit
    assert "2" in msg  # used


def test_counter_resets_between_runs():
    fn = _make_fn(1)
    with RunContext():
        assert fn() == "ok"
        with pytest.raises(MaxAttemptsExceeded):
            fn()

    # Second run gets a fresh counter
    with RunContext():
        assert fn() == "ok"


def test_failed_tool_still_increments():
    """Even if the tool raises, the attempt still counts."""

    @max_attempts(calls=2)
    def flaky():
        raise ValueError("boom")

    with RunContext():
        with pytest.raises(ValueError):
            flaky()
        with pytest.raises(ValueError):
            flaky()
        # Third call: max_attempts fires before fn
        with pytest.raises(MaxAttemptsExceeded):
            flaky()


def test_independent_counters_per_tool():
    @max_attempts(calls=1)
    def fn_a():
        return "a"

    @max_attempts(calls=1)
    def fn_b():
        return "b"

    with RunContext():
        assert fn_a() == "a"
        assert fn_b() == "b"
        with pytest.raises(MaxAttemptsExceeded):
            fn_a()
        with pytest.raises(MaxAttemptsExceeded):
            fn_b()


# ---------------------------------------------------------------------------
# Integration — async autodetect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_allows_up_to_limit():
    @max_attempts(calls=2)
    async def fn():
        return "async-ok"

    async with RunContext():
        assert await fn() == "async-ok"
        assert await fn() == "async-ok"


@pytest.mark.asyncio
async def test_async_raises_on_exceeding():
    @max_attempts(calls=1)
    async def fn():
        return "ok"

    async with RunContext():
        await fn()
        with pytest.raises(MaxAttemptsExceeded):
            await fn()


@pytest.mark.asyncio
async def test_async_counter_resets_between_runs():
    @max_attempts(calls=1)
    async def fn():
        return "ok"

    async with RunContext():
        assert await fn() == "ok"

    async with RunContext():
        assert await fn() == "ok"


# ---------------------------------------------------------------------------
# Integration — functools.wraps preservation
# ---------------------------------------------------------------------------


def test_functools_wraps_preserves_name():
    @max_attempts(calls=5)
    def my_special_tool():
        pass

    assert my_special_tool.__name__ == "my_special_tool"


def test_functools_wraps_preserves_qualname():
    @max_attempts(calls=5)
    def my_special_tool():
        pass

    expected = "test_functools_wraps_preserves_qualname.<locals>.my_special_tool"
    assert my_special_tool.__qualname__ == expected


# ---------------------------------------------------------------------------
# Integration — unified @tool decorator
# ---------------------------------------------------------------------------


def test_tool_decorator_max_attempts_kwarg():
    @actguard.tool(max_attempts={"calls": 2})
    def fn():
        return "ok"

    with RunContext():
        assert fn() == "ok"
        assert fn() == "ok"
        with pytest.raises(MaxAttemptsExceeded):
            fn()


def test_tool_decorator_stacked_with_rate_limit():
    @actguard.tool(
        max_attempts={"calls": 2},
        rate_limit={"max_calls": 10, "period": 60},
    )
    def fn():
        return "ok"

    with RunContext():
        assert fn() == "ok"
        assert fn() == "ok"
        with pytest.raises(MaxAttemptsExceeded):
            fn()


def test_tool_decorator_stacked_with_circuit_breaker():
    @actguard.tool(
        max_attempts={"calls": 3},
        circuit_breaker={"name": "my-dep", "max_fails": 5},
    )
    def fn():
        return "ok"

    with RunContext():
        assert fn() == "ok"
        assert fn() == "ok"
        assert fn() == "ok"
        with pytest.raises(MaxAttemptsExceeded):
            fn()


# ---------------------------------------------------------------------------
# Safety — missing context
# ---------------------------------------------------------------------------


def test_missing_context_raises_missing_runtime_context_error():
    fn = _make_fn(5)
    with pytest.raises(MissingRuntimeContextError):
        fn()


def test_missing_runtime_context_error_message():
    fn = _make_fn(5)
    with pytest.raises(MissingRuntimeContextError) as exc_info:
        fn()
    assert "RunContext" in str(exc_info.value)


def test_missing_runtime_context_error_is_tool_guard_error():
    from actguard.exceptions import ToolGuardError

    fn = _make_fn(5)
    with pytest.raises(ToolGuardError):
        fn()


# ---------------------------------------------------------------------------
# Safety — thread safety
# ---------------------------------------------------------------------------


def test_thread_safety_exactly_n_ok():
    """100 threads, calls=50 → exactly 50 ok + 50 exceeded.

    threading.Thread does not inherit ContextVar state automatically, so we
    capture the current context with copy_context() and run each worker inside
    it. All copies point to the same RunState object, so the shared
    _tool_attempts dict (protected by _lock) is correctly incremented across
    all threads.
    """
    import contextvars

    THREADS = 100
    LIMIT = 50

    fn = _make_fn(LIMIT)

    results = []
    lock = threading.Lock()

    with RunContext():
        ctx = contextvars.copy_context()

        def worker():
            def _work():
                try:
                    fn()
                    with lock:
                        results.append("ok")
                except MaxAttemptsExceeded:
                    with lock:
                        results.append("exceeded")

            ctx.run(_work)

        threads = [threading.Thread(target=worker) for _ in range(THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    ok_count = results.count("ok")
    exceeded_count = results.count("exceeded")
    assert ok_count == LIMIT
    assert exceeded_count == THREADS - LIMIT


# ---------------------------------------------------------------------------
# Introspection — RunContext
# ---------------------------------------------------------------------------


def test_run_context_get_attempt_count():
    fn = _make_fn(10)
    with RunContext() as ctx:
        tool_id = f"{fn.__wrapped__.__module__}:{fn.__wrapped__.__qualname__}"
        assert ctx.get_attempt_count(tool_id) == 0
        fn()
        assert ctx.get_attempt_count(tool_id) == 1
        fn()
        assert ctx.get_attempt_count(tool_id) == 2


def test_run_context_auto_uuid_is_unique():
    ctx1 = RunContext()
    ctx2 = RunContext()
    assert ctx1.run_id != ctx2.run_id


def test_run_context_explicit_run_id_preserved():
    ctx = RunContext(run_id="my-explicit-id")
    assert ctx.run_id == "my-explicit-id"

    with ctx:
        pass  # enter/exit should not change run_id

    assert ctx.run_id == "my-explicit-id"


def test_run_context_run_id_in_exception():
    fn = _make_fn(1)
    with RunContext(run_id="explicit-run"):
        fn()
        with pytest.raises(MaxAttemptsExceeded) as exc_info:
            fn()

    assert exc_info.value.run_id == "explicit-run"


# ---------------------------------------------------------------------------
# Validation — bad arguments
# ---------------------------------------------------------------------------


def test_calls_zero_raises_value_error():
    with pytest.raises(ValueError, match="calls must be an integer"):
        max_attempts(calls=0)


def test_calls_negative_raises_value_error():
    with pytest.raises(ValueError, match="calls must be an integer"):
        max_attempts(calls=-1)


def test_calls_float_raises_value_error():
    with pytest.raises(ValueError, match="calls must be an integer"):
        max_attempts(calls=1.5)


def test_calls_bool_raises_value_error():
    with pytest.raises(ValueError, match="calls must be an integer"):
        max_attempts(calls=True)


# ---------------------------------------------------------------------------
# Decorator usage — both styles
# ---------------------------------------------------------------------------


def test_decorator_with_parens_style():
    @max_attempts(calls=2)
    def fn():
        return "ok"

    with RunContext():
        assert fn() == "ok"
        assert fn() == "ok"
        with pytest.raises(MaxAttemptsExceeded):
            fn()


def test_decorator_explicit_fn_argument():
    def fn():
        return "ok"

    guarded = max_attempts(fn, calls=2)

    with RunContext():
        assert guarded() == "ok"
        assert guarded() == "ok"
        with pytest.raises(MaxAttemptsExceeded):
            guarded()


# ---------------------------------------------------------------------------
# Nested RunContext — inner context is independent
# ---------------------------------------------------------------------------


def test_nested_run_contexts_are_independent():
    fn = _make_fn(1)

    with RunContext():
        assert fn() == "ok"
        with RunContext():
            # Inner context has fresh state
            assert fn() == "ok"
            with pytest.raises(MaxAttemptsExceeded):
                fn()
        # Outer context is restored — still exhausted
        with pytest.raises(MaxAttemptsExceeded):
            fn()


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


def test_public_imports():
    assert hasattr(actguard, "RunContext")
    assert hasattr(actguard, "max_attempts")
    assert hasattr(actguard, "MaxAttemptsExceeded")
    assert hasattr(actguard, "MissingRuntimeContextError")


def test_max_attempts_exceeded_in_all():
    assert "MaxAttemptsExceeded" in actguard.__all__


def test_missing_runtime_context_error_in_all():
    assert "MissingRuntimeContextError" in actguard.__all__


def test_run_context_in_all():
    assert "RunContext" in actguard.__all__


def test_max_attempts_in_all():
    assert "max_attempts" in actguard.__all__
