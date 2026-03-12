"""Tests for the @rate_limit decorator."""
import base64
import json

import pytest

import actguard
from actguard.exceptions import RateLimitExceeded, ScopeValidationError
from actguard.tools.rate_limit import rate_limit

# ---------------------------------------------------------------------------
# Sync — basic allow / block
# ---------------------------------------------------------------------------


def test_allows_calls_up_to_max():
    @rate_limit(max_calls=3, period=60)
    def fn():
        return "ok"

    assert fn() == "ok"
    assert fn() == "ok"
    assert fn() == "ok"


def test_raises_on_exceeding_sync():
    @rate_limit(max_calls=2, period=60)
    def fn():
        return "ok"

    fn()
    fn()
    with pytest.raises(RateLimitExceeded):
        fn()


# ---------------------------------------------------------------------------
# Async — block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raises_on_exceeding_async():
    @rate_limit(max_calls=2, period=60)
    async def fn():
        return "ok"

    await fn()
    await fn()
    with pytest.raises(RateLimitExceeded):
        await fn()


@pytest.mark.asyncio
async def test_async_allows_up_to_max():
    @rate_limit(max_calls=3, period=60)
    async def fn():
        return "async-ok"

    assert await fn() == "async-ok"
    assert await fn() == "async-ok"
    assert await fn() == "async-ok"


# ---------------------------------------------------------------------------
# Scope — global vs per-user
# ---------------------------------------------------------------------------


def test_no_scope_global_counter():
    """scope=None: all callers share one counter."""

    @rate_limit(max_calls=2, period=60)
    def fn(user_id: str):
        return user_id

    fn("alice")
    fn("bob")  # different arg, but no scope — shares same counter
    with pytest.raises(RateLimitExceeded):
        fn("charlie")


def test_scope_per_user_independent():
    """scope='user_id': each distinct value gets its own counter."""

    @rate_limit(max_calls=2, period=60, scope="user_id")
    def fn(user_id: str):
        return user_id

    fn("alice")
    fn("alice")
    # alice is now exhausted
    with pytest.raises(RateLimitExceeded):
        fn("alice")

    # bob has a fresh counter
    fn("bob")
    fn("bob")
    with pytest.raises(RateLimitExceeded):
        fn("bob")


def test_scope_different_users_independent():
    """Calls for different scoped users do not affect each other's limits."""

    @rate_limit(max_calls=1, period=60, scope="user_id")
    def fn(user_id: str):
        return user_id

    fn("alice")
    # alice exhausted — bob still allowed
    fn("bob")
    fn("carol")

    with pytest.raises(RateLimitExceeded):
        fn("alice")


# ---------------------------------------------------------------------------
# Scope — invalid argument raises at decoration time
# ---------------------------------------------------------------------------


def test_invalid_scope_raises_at_decoration_time():
    with pytest.raises(ScopeValidationError, match="scope="):

        @rate_limit(max_calls=5, period=60, scope="nonexistent_param")
        def fn(user_id: str):
            return user_id


def test_valid_scope_does_not_raise_at_decoration_time():
    # Should not raise
    @rate_limit(max_calls=5, period=60, scope="user_id")
    def fn(user_id: str):
        return user_id


# ---------------------------------------------------------------------------
# retry_after
# ---------------------------------------------------------------------------


def test_retry_after_positive_and_leq_period():
    period = 60.0

    @rate_limit(max_calls=1, period=period)
    def fn():
        pass

    fn()
    with pytest.raises(RateLimitExceeded) as exc_info:
        fn()

    exc = exc_info.value
    assert exc.retry_after > 0
    assert exc.retry_after <= period


def test_retry_after_in_exception_message():
    @rate_limit(max_calls=1, period=60)
    def fn():
        pass

    fn()
    with pytest.raises(RateLimitExceeded) as exc_info:
        fn()

    assert "Retry after" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Return value + functools.wraps
# ---------------------------------------------------------------------------


def test_return_value_preserved():
    @rate_limit(max_calls=5, period=60)
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    assert add(2, 3) == 5


def test_functools_wraps_preserves_name_and_doc():
    @rate_limit(max_calls=5, period=60)
    def my_func():
        """My docstring."""
        pass

    assert my_func.__name__ == "my_func"
    assert my_func.__doc__ == "My docstring."


@pytest.mark.asyncio
async def test_async_return_value_preserved():
    @rate_limit(max_calls=5, period=60)
    async def greet(name: str) -> str:
        return f"hello {name}"

    assert await greet("world") == "hello world"


# ---------------------------------------------------------------------------
# @actguard.tool unified decorator
# ---------------------------------------------------------------------------


def test_tool_unified_decorator_rate_limit():
    @actguard.tool(rate_limit={"max_calls": 3, "period": 60})
    def fn():
        return "ok"

    assert fn() == "ok"
    assert fn() == "ok"
    assert fn() == "ok"
    with pytest.raises(RateLimitExceeded):
        fn()


def test_tool_unified_no_guards():
    """@actguard.tool() with no guards applied just returns the function."""

    @actguard.tool()
    def fn():
        return "bare"

    assert fn() == "bare"


def test_tool_unified_decorator_with_scope():
    @actguard.tool(rate_limit={"max_calls": 1, "period": 60, "scope": "user_id"})
    def send_email(user_id: str, subject: str) -> str:
        return f"sent to {user_id}"

    assert send_email("alice", "hi") == "sent to alice"
    with pytest.raises(RateLimitExceeded):
        send_email("alice", "bye")
    # Different scope partition
    assert send_email("bob", "hi") == "sent to bob"


# ---------------------------------------------------------------------------
# Client config loading
# ---------------------------------------------------------------------------


def test_client_from_file_json(tmp_path):
    config_file = tmp_path / "actguard.json"
    config_data = {
        "gateway_url": "https://api.actguard.io",
        "api_key": "sk-test",
    }
    config_file.write_text(json.dumps(config_data))

    client = actguard.Client.from_file(str(config_file))

    assert client.gateway_url == "https://api.actguard.io"
    assert client.api_key == "sk-test"


def test_client_from_env_base64_string(monkeypatch):
    config_data = {
        "gateway_url": "https://gw.example.com",
        "api_key": "k1",
    }
    encoded = base64.b64encode(json.dumps(config_data).encode()).decode()
    monkeypatch.setenv("ACTGUARD_CONFIG", encoded)

    client = actguard.Client.from_env()

    assert client.gateway_url == "https://gw.example.com"
    assert client.api_key == "k1"


# ---------------------------------------------------------------------------
# Exception attributes
# ---------------------------------------------------------------------------


def test_rate_limit_exceeded_attributes():
    @rate_limit(max_calls=1, period=30, scope="user_id")
    def fn(user_id: str):
        pass

    fn("alice")
    with pytest.raises(RateLimitExceeded) as exc_info:
        fn("alice")

    exc = exc_info.value
    assert exc.func_name.endswith("fn")
    assert exc.scope_value == "alice"
    assert exc.max_calls == 1
    assert exc.period == 30
    assert 0 < exc.retry_after <= 30


def test_rate_limit_exceeded_is_tool_guard_error():
    from actguard.exceptions import ToolGuardError

    @rate_limit(max_calls=1, period=60)
    def fn():
        pass

    fn()
    with pytest.raises(ToolGuardError):
        fn()
