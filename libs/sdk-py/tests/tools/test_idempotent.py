"""Tests for the @idempotent decorator."""

import contextvars
import threading
import time

import pytest

import actguard
from actguard.exceptions import (
    DuplicateIdempotencyKey,
    IdempotencyInProgress,
    IdempotencyOutcomeUnknown,
    InvalidIdempotentToolError,
    MissingIdempotencyKeyError,
    MissingRuntimeContextError,
)
from actguard.tools.idempotent import idempotent

_CLIENT = actguard.Client()

# ---------------------------------------------------------------------------
# Test 1: Basic deduplication (sync)
# ---------------------------------------------------------------------------


def test_basic_deduplication_sync():
    call_count = 0

    @idempotent
    def my_tool(idempotency_key: str):
        nonlocal call_count
        call_count += 1
        return "result"

    with _CLIENT.run():
        r1 = my_tool(idempotency_key="key-1")
        r2 = my_tool(idempotency_key="key-1")

    assert call_count == 1
    assert r1 == "result"
    assert r2 == "result"


# ---------------------------------------------------------------------------
# Test 2: Basic deduplication (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_deduplication_async():
    call_count = 0

    @idempotent
    async def my_async_tool(idempotency_key: str):
        nonlocal call_count
        call_count += 1
        return "async-result"

    async with _CLIENT.run():
        r1 = await my_async_tool(idempotency_key="key-async")
        r2 = await my_async_tool(idempotency_key="key-async")

    assert call_count == 1
    assert r1 == "async-result"
    assert r2 == "async-result"


# ---------------------------------------------------------------------------
# Test 3: Concurrency — IN_PROGRESS raises
# ---------------------------------------------------------------------------


def test_concurrency_in_progress_raises():
    """Thread 1 sleeps inside tool; Thread 2 (same key) raises IdempotencyInProgress.

    Each thread gets its own copy of the Context (required — a Context object
    cannot be entered by two threads simultaneously). Both copies point to the
    same RunState object, so _idem_store is shared and IN_PROGRESS is visible
    across threads.
    """
    barrier = threading.Barrier(2)
    thread2_exception = []

    @idempotent
    def slow_tool(idempotency_key: str):
        # Thread 1 reaches here after setting IN_PROGRESS. Signal thread 2.
        barrier.wait()
        time.sleep(0.05)
        return "done"

    with _CLIENT.run():
        # Two separate copies — same RunState reference, different Context objects.
        ctx1 = contextvars.copy_context()
        ctx2 = contextvars.copy_context()

        def thread1():
            ctx1.run(slow_tool, idempotency_key="shared-key")

        def thread2():
            # Wait until thread 1 is inside the tool (IN_PROGRESS already set).
            barrier.wait()
            try:
                ctx2.run(slow_tool, idempotency_key="shared-key")
            except IdempotencyInProgress as e:
                thread2_exception.append(e)

        t1 = threading.Thread(target=thread1)
        t2 = threading.Thread(target=thread2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert len(thread2_exception) == 1
    assert thread2_exception[0].key == "shared-key"


# ---------------------------------------------------------------------------
# Test 4: on_duplicate="raise"
# ---------------------------------------------------------------------------


def test_on_duplicate_raise():
    @idempotent(on_duplicate="raise")
    def my_tool(idempotency_key: str):
        return "result"

    with _CLIENT.run():
        my_tool(idempotency_key="key-dup")
        with pytest.raises(DuplicateIdempotencyKey) as exc_info:
            my_tool(idempotency_key="key-dup")

    err = exc_info.value
    assert err.key == "key-dup"
    assert "key-dup" in str(err)


# ---------------------------------------------------------------------------
# Test 5: Unsafe failure → UNKNOWN
# ---------------------------------------------------------------------------


def test_unsafe_failure_blocks_retry():
    call_count = 0

    @idempotent
    def my_tool(idempotency_key: str):
        nonlocal call_count
        call_count += 1
        raise ConnectionError("network down")

    with _CLIENT.run():
        with pytest.raises(ConnectionError):
            my_tool(idempotency_key="key-unsafe")

        with pytest.raises(IdempotencyOutcomeUnknown) as exc_info:
            my_tool(idempotency_key="key-unsafe")

    assert call_count == 1
    err = exc_info.value
    assert err.key == "key-unsafe"
    assert err.last_error_type is ConnectionError


# ---------------------------------------------------------------------------
# Test 6: Safe failure → retry allowed
# ---------------------------------------------------------------------------


def test_safe_failure_allows_retry():
    call_count = 0

    @idempotent(safe_exceptions=(ValueError,))
    def my_tool(idempotency_key: str):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("safe error")
        return "ok"

    with _CLIENT.run():
        with pytest.raises(ValueError):
            my_tool(idempotency_key="key-safe")

        result = my_tool(idempotency_key="key-safe")

    assert call_count == 2
    assert result == "ok"


# ---------------------------------------------------------------------------
# Test 7: TTL expiry → re-execution
# ---------------------------------------------------------------------------


def test_ttl_expiry_allows_reexecution(monkeypatch):
    call_count = 0
    fake_time = [0.0]

    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    @idempotent(ttl_s=60.0)
    def my_tool(idempotency_key: str):
        nonlocal call_count
        call_count += 1
        return f"result-{call_count}"

    with _CLIENT.run():
        r1 = my_tool(idempotency_key="key-ttl")
        assert call_count == 1
        assert r1 == "result-1"

        # Advance time past TTL
        fake_time[0] = 61.0

        r2 = my_tool(idempotency_key="key-ttl")
        assert call_count == 2
        assert r2 == "result-2"


# ---------------------------------------------------------------------------
# Test 8: Missing idempotency_key param → decoration-time error
# ---------------------------------------------------------------------------


def test_missing_param_raises_at_decoration_time():
    with pytest.raises(InvalidIdempotentToolError):

        @idempotent
        def bad_tool(x: int):
            return x


# ---------------------------------------------------------------------------
# Test 9: None key → MissingIdempotencyKeyError
# ---------------------------------------------------------------------------


def test_none_key_raises_missing_idempotency_key_error():
    @idempotent
    def my_tool(idempotency_key: str):
        return "ok"

    with _CLIENT.run():
        with pytest.raises(MissingIdempotencyKeyError) as exc_info:
            my_tool(idempotency_key=None)

    assert "idempotency_key" in str(exc_info.value).lower() or exc_info.value.tool_name


def test_empty_string_key_raises_missing_idempotency_key_error():
    @idempotent
    def my_tool(idempotency_key: str):
        return "ok"

    with _CLIENT.run():
        with pytest.raises(MissingIdempotencyKeyError):
            my_tool(idempotency_key="")


# ---------------------------------------------------------------------------
# Test 10: No active client.run(...) → MissingRuntimeContextError
# ---------------------------------------------------------------------------


def test_no_active_runtime_context_raises():
    @idempotent
    def my_tool(idempotency_key: str):
        return "ok"

    with pytest.raises(MissingRuntimeContextError):
        my_tool(idempotency_key="key-1")


# ---------------------------------------------------------------------------
# Test 11: @tool() unified integration
# ---------------------------------------------------------------------------


def test_tool_unified_decorator_integration():
    call_count = 0

    @actguard.tool(idempotent={"ttl_s": 60})
    def my_tool(idempotency_key: str):
        nonlocal call_count
        call_count += 1
        return "ok"

    with _CLIENT.run():
        r1 = my_tool(idempotency_key="unified-key")
        r2 = my_tool(idempotency_key="unified-key")

    assert call_count == 1
    assert r1 == "ok"
    assert r2 == "ok"


# ---------------------------------------------------------------------------
# Additional: different keys are independent
# ---------------------------------------------------------------------------


def test_different_keys_are_independent():
    call_count = 0

    @idempotent
    def my_tool(idempotency_key: str):
        nonlocal call_count
        call_count += 1
        return call_count

    with _CLIENT.run():
        r1 = my_tool(idempotency_key="key-a")
        r2 = my_tool(idempotency_key="key-b")
        r3 = my_tool(idempotency_key="key-a")

    assert call_count == 2
    assert r1 == 1
    assert r2 == 2
    assert r3 == 1  # cached


# ---------------------------------------------------------------------------
# Additional: functools.wraps preservation
# ---------------------------------------------------------------------------


def test_functools_wraps_preserved():
    @idempotent
    def my_special_tool(idempotency_key: str):
        """Docstring."""
        return "ok"

    assert my_special_tool.__name__ == "my_special_tool"
    assert my_special_tool.__doc__ == "Docstring."


# ---------------------------------------------------------------------------
# Additional: state resets between runs
# ---------------------------------------------------------------------------


def test_state_resets_between_runs():
    call_count = 0

    @idempotent
    def my_tool(idempotency_key: str):
        nonlocal call_count
        call_count += 1
        return call_count

    with _CLIENT.run():
        my_tool(idempotency_key="key-1")

    with _CLIENT.run():
        # New run → fresh state → tool runs again
        result = my_tool(idempotency_key="key-1")

    assert call_count == 2
    assert result == 2


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


def test_public_imports():
    assert hasattr(actguard, "idempotent")
    assert hasattr(actguard, "ActGuardError")
    assert hasattr(actguard, "ActGuardToolError")
    assert hasattr(actguard, "ActGuardPaymentRequired")


def test_new_symbols_in_all():
    assert "idempotent" in actguard.__all__
    assert "ActGuardError" in actguard.__all__
    assert "ActGuardToolError" in actguard.__all__
    assert "ActGuardPaymentRequired" in actguard.__all__
