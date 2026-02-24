"""Tests for the @rate_limit decorator."""
import base64
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import actguard
from actguard.exceptions import RateLimitExceeded
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
    with pytest.raises(ValueError, match="scope="):

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
# Gateway stub — report_event called
# ---------------------------------------------------------------------------


def _get_rate_limit_module():
    """Return the actguard.tools.rate_limit module object (not the re-exported function)."""
    import sys

    return sys.modules["actguard.tools.rate_limit"]


def test_report_event_called_on_allowed():
    rl_mod = _get_rate_limit_module()

    with patch.object(rl_mod, "report_event") as mock_report:

        @rate_limit(max_calls=5, period=60)
        def fn():
            pass

        fn()

    mock_report.assert_called_once()
    event = mock_report.call_args[0][0]
    assert event["type"] == "rate_limit_check"
    assert event["allowed"] is True


def test_report_event_called_on_blocked():
    rl_mod = _get_rate_limit_module()

    with patch.object(rl_mod, "report_event") as mock_report:

        @rate_limit(max_calls=1, period=60)
        def fn():
            pass

        fn()
        with pytest.raises(RateLimitExceeded):
            fn()

    assert mock_report.call_count == 2
    blocked_event = mock_report.call_args[0][0]
    assert blocked_event["type"] == "rate_limit_exceeded"
    assert blocked_event["allowed"] is False
    assert "retry_after" in blocked_event


def test_report_event_noop_when_no_config():
    """Default behavior: no config → report_event is a no-op (no errors)."""
    import actguard._config as cfg_mod

    assert cfg_mod._config is None  # fresh_cache fixture ensures this

    @rate_limit(max_calls=5, period=60)
    def fn():
        return "ok"

    # Should not raise even though report_event calls gateway
    fn()


# ---------------------------------------------------------------------------
# actguard.configure() — JSON file
# ---------------------------------------------------------------------------


def test_configure_json_file(tmp_path):
    config_file = tmp_path / "actguard.json"
    config_data = {
        "agent_id": "test-agent",
        "gateway_url": "https://api.actguard.io",
        "api_key": "sk-test",
    }
    config_file.write_text(json.dumps(config_data))

    actguard.configure(str(config_file))

    import actguard._config as cfg_mod

    assert cfg_mod._config is not None
    assert cfg_mod._config.agent_id == "test-agent"
    assert cfg_mod._config.gateway_url == "https://api.actguard.io"
    assert cfg_mod._config.api_key == "sk-test"


def test_configure_base64_string():
    config_data = {"agent_id": "b64-agent", "gateway_url": "https://gw.example.com"}
    encoded = base64.b64encode(json.dumps(config_data).encode()).decode()

    actguard.configure(encoded)

    import actguard._config as cfg_mod

    assert cfg_mod._config is not None
    assert cfg_mod._config.agent_id == "b64-agent"
    assert cfg_mod._config.gateway_url == "https://gw.example.com"
    assert cfg_mod._config.api_key is None


def test_configure_none_clears_config():
    actguard.configure(
        base64.b64encode(json.dumps({"agent_id": "x"}).encode()).decode()
    )
    import actguard._config as cfg_mod

    assert cfg_mod._config is not None
    actguard.configure(None)
    assert cfg_mod._config is None


def test_configure_env_var(monkeypatch):
    config_data = {"agent_id": "env-agent"}
    encoded = base64.b64encode(json.dumps(config_data).encode()).decode()
    monkeypatch.setenv("ACTGUARD_CONFIG", encoded)

    actguard.configure()  # no explicit arg — reads from env

    import actguard._config as cfg_mod

    assert cfg_mod._config is not None
    assert cfg_mod._config.agent_id == "env-agent"


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
