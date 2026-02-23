"""Tests for BudgetGuard core behaviour (no real LLM calls)."""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import actguard.integrations.openai as _oai_mod
from actguard import BudgetGuard, BudgetExceededError
from actguard.core.state import get_current_state
from actguard.core.pricing import get_cost


# ---------------------------------------------------------------------------
# Fake response helpers
# ---------------------------------------------------------------------------

def _resp(prompt_tokens, completion_tokens, content="Once upon a time..."):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def _chunk(prompt_tokens=None, completion_tokens=None, content=""):
    """Stream chunk. usage is None on intermediate chunks, populated on last."""
    usage = None if prompt_tokens is None else SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))],
        usage=usage,
    )


class _AsyncIter:
    """Async iterable wrapper for testing."""

    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        return self._aiter_impl()

    async def _aiter_impl(self):
        for item in self._items:
            yield item


# ---------------------------------------------------------------------------
# OpenAI mock fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def openai_mocks():
    from openai._base_client import SyncAPIClient, AsyncAPIClient

    # Save original state
    orig_request = SyncAPIClient.request
    orig_async_request = AsyncAPIClient.request
    orig_patched = _oai_mod._patched

    # Install mocks so patch_openai() captures them as _orig_request
    sync_mock = MagicMock()
    async_mock = AsyncMock()
    SyncAPIClient.request = sync_mock
    AsyncAPIClient.request = async_mock

    # Reset so patch_openai() will run on the next BudgetGuard.__enter__()
    _oai_mod._patched = False

    yield sync_mock, async_mock

    # Restore original state
    SyncAPIClient.request = orig_request
    AsyncAPIClient.request = orig_async_request
    _oai_mod._patched = orig_patched


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

class TestPricing:
    def test_known_model(self):
        # gpt-4o: $2.50 input / $10.00 output per 1M tokens
        cost = get_cost("openai", "gpt-4o", 1_000_000, 0)
        assert cost == pytest.approx(2.50)
        cost = get_cost("openai", "gpt-4o", 0, 1_000_000)
        assert cost == pytest.approx(10.00)

    def test_anthropic_model(self):
        # claude-3-haiku: $0.25 / $1.25 per 1M
        cost = get_cost("anthropic", "claude-3-haiku-20240307", 1_000_000, 1_000_000)
        assert cost == pytest.approx(1.50)

    def test_unknown_model_warns_and_returns_zero(self):
        with pytest.warns(UserWarning, match="no pricing entry"):
            cost = get_cost("openai", "gpt-99-ultra", 100_000, 100_000)
        assert cost == 0.0

    def test_unknown_provider_warns(self):
        with pytest.warns(UserWarning, match="no pricing entry"):
            cost = get_cost("mystery_provider", "model-x", 1000, 1000)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# BudgetGuard context manager
# ---------------------------------------------------------------------------

class TestBudgetGuard:
    def test_state_set_and_cleared(self):
        assert get_current_state() is None
        with BudgetGuard(user_id="alice") as g:
            state = get_current_state()
            assert state is not None
            assert state.user_id == "alice"
        assert get_current_state() is None

    def test_nesting_restores_outer_state(self):
        with BudgetGuard(user_id="outer") as outer:
            with BudgetGuard(user_id="inner") as inner:
                assert get_current_state().user_id == "inner"
            assert get_current_state().user_id == "outer"
        assert get_current_state() is None

    def test_exception_propagated_and_state_cleared(self):
        with pytest.raises(ValueError):
            with BudgetGuard(user_id="alice") as g:
                raise ValueError("boom")
        assert get_current_state() is None


# ---------------------------------------------------------------------------
# Async context manager
# ---------------------------------------------------------------------------

class TestBudgetGuardAsync:
    async def test_async_nesting(self):
        async with BudgetGuard(user_id="outer") as outer:
            async with BudgetGuard(user_id="inner") as inner:
                assert get_current_state().user_id == "inner"
            assert get_current_state().user_id == "outer"
        assert get_current_state() is None


# ---------------------------------------------------------------------------
# OpenAI helper unit tests
# ---------------------------------------------------------------------------

class TestOpenAIHelpers:
    def test_get_model_from_none_json_data(self):
        """Risk 1: json_data=None (GET requests) must not raise."""
        from actguard.integrations.openai import _get_model_from_options
        options = SimpleNamespace(json_data=None)
        assert _get_model_from_options(options) == ""

    def test_inject_stream_options_scoped_to_chat_completions(self):
        """Risk 2: stream_options injected for /chat/completions, not for /responses."""
        from actguard.integrations.openai import _inject_stream_options

        chat_opts = SimpleNamespace(url="/chat/completions", json_data={"model": "gpt-4o"})
        _inject_stream_options(chat_opts)
        assert chat_opts.json_data.get("stream_options") == {"include_usage": True}

        resp_opts = SimpleNamespace(url="/responses", json_data={"model": "gpt-4o"})
        _inject_stream_options(resp_opts)
        assert "stream_options" not in resp_opts.json_data

        none_opts = SimpleNamespace(url="/models", json_data=None)
        _inject_stream_options(none_opts)  # must not raise


# ---------------------------------------------------------------------------
# OpenAI integration tests
# ---------------------------------------------------------------------------

class TestOpenAIIntegration:
    # gpt-4o: $2.50/1M input, $10.00/1M output
    # 100 prompt + 50 completion = (100*2.50 + 50*10.00) / 1_000_000 = 0.00075
    _EXPECTED_COST = (100 * 2.50 + 50 * 10.00) / 1_000_000

    def test_sync_non_streaming_records_usage(self, openai_mocks):
        import openai
        sync_mock, _ = openai_mocks
        sync_mock.return_value = _resp(100, 50)

        client = openai.OpenAI(api_key="sk-test")
        with BudgetGuard(user_id="u1") as guard:
            client.chat.completions.create(model="gpt-4o", messages=[])

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    async def test_async_non_streaming_records_usage(self, openai_mocks):
        import openai
        _, async_mock = openai_mocks
        async_mock.return_value = _resp(100, 50)

        client = openai.AsyncOpenAI(api_key="sk-test")
        async with BudgetGuard(user_id="u1") as guard:
            await client.chat.completions.create(model="gpt-4o", messages=[])

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_sync_streaming_records_usage(self, openai_mocks):
        import openai
        sync_mock, _ = openai_mocks
        sync_mock.return_value = iter([_chunk(content="a"), _chunk(100, 50)])

        client = openai.OpenAI(api_key="sk-test")
        with BudgetGuard(user_id="u1") as guard:
            stream = client.chat.completions.create(
                model="gpt-4o", messages=[], stream=True
            )
            for _ in stream:
                pass

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    async def test_async_streaming_records_usage(self, openai_mocks):
        import openai
        _, async_mock = openai_mocks
        async_mock.return_value = _AsyncIter([_chunk(content="a"), _chunk(100, 50)])

        client = openai.AsyncOpenAI(api_key="sk-test")
        async with BudgetGuard(user_id="u1") as guard:
            stream = await client.chat.completions.create(
                model="gpt-4o", messages=[], stream=True
            )
            async for _ in stream:
                pass

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_token_limit_exceeded(self, openai_mocks):
        import openai
        sync_mock, _ = openai_mocks
        sync_mock.return_value = _resp(200, 0)

        client = openai.OpenAI(api_key="sk-test")
        with pytest.raises(BudgetExceededError) as exc_info:
            with BudgetGuard(user_id="u1", token_limit=100):
                client.chat.completions.create(model="gpt-4o", messages=[])
        assert exc_info.value.limit_type == "token"

    def test_usd_limit_exceeded(self, openai_mocks):
        import openai
        sync_mock, _ = openai_mocks
        sync_mock.return_value = _resp(1_000_000, 0)

        client = openai.OpenAI(api_key="sk-test")
        with pytest.raises(BudgetExceededError) as exc_info:
            with BudgetGuard(user_id="u1", usd_limit=1.0):
                client.chat.completions.create(model="gpt-4o", messages=[])
        assert exc_info.value.limit_type == "usd"
