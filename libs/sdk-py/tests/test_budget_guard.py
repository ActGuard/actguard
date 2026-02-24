"""Tests for BudgetGuard core behaviour (no real LLM calls)."""
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import actguard.integrations.openai as _oai_mod
import actguard.integrations.anthropic as _ant_mod
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


@pytest.fixture
def anthropic_mocks():
    from anthropic._base_client import SyncAPIClient, AsyncAPIClient

    orig_request = SyncAPIClient.request
    orig_async_request = AsyncAPIClient.request
    orig_patched = _ant_mod._patched

    sync_mock = MagicMock()
    async_mock = AsyncMock()
    SyncAPIClient.request = sync_mock
    AsyncAPIClient.request = async_mock
    _ant_mod._patched = False

    yield sync_mock, async_mock

    SyncAPIClient.request = orig_request
    AsyncAPIClient.request = orig_async_request
    _ant_mod._patched = orig_patched


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
    def test_parse_major_minor_prerelease(self):
        from actguard.integrations.openai import _parse_major_minor
        assert _parse_major_minor("1.76.0rc1") == (1, 76)

    def test_parse_major_minor_invalid(self):
        from actguard.integrations.openai import _parse_major_minor
        assert _parse_major_minor("v1") is None

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

    async def test_async_stream_manual_anext_without_aiter(self):
        from actguard.core.state import BudgetState
        from actguard.integrations.openai import _WrappedAsyncStream

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=None)
        stream = _WrappedAsyncStream(_AsyncIter([_chunk(100, 50)]), "gpt-4o", state)

        first = await anext(stream)
        assert first is not None
        assert state.tokens_used == 150


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


class TestAnthropicIntegration:
    _EXPECTED_COST = (100 * 0.25 + 50 * 1.25) / 1_000_000

    @staticmethod
    def _resp(input_tokens: int, output_tokens: int):
        return SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )

    @staticmethod
    def _event_start(input_tokens: int):
        return SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=input_tokens),
            ),
        )

    @staticmethod
    def _event_delta(output_tokens: int):
        return SimpleNamespace(
            type="message_delta",
            usage=SimpleNamespace(output_tokens=output_tokens),
        )

    def test_sync_non_streaming_records_usage(self, anthropic_mocks):
        import anthropic

        sync_mock, _ = anthropic_mocks
        sync_mock.return_value = self._resp(100, 50)

        client = anthropic.Anthropic(api_key="test")
        with BudgetGuard(user_id="u1") as guard:
            client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=100,
                messages=[{"role": "user", "content": "hi"}],
            )

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    async def test_async_non_streaming_records_usage(self, anthropic_mocks):
        import anthropic

        _, async_mock = anthropic_mocks
        async_mock.return_value = self._resp(100, 50)

        client = anthropic.AsyncAnthropic(api_key="test")
        async with BudgetGuard(user_id="u1") as guard:
            await client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=100,
                messages=[{"role": "user", "content": "hi"}],
            )

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_sync_streaming_records_usage(self, anthropic_mocks):
        import anthropic

        sync_mock, _ = anthropic_mocks
        sync_mock.return_value = iter([self._event_start(100), self._event_delta(50)])

        client = anthropic.Anthropic(api_key="test")
        with BudgetGuard(user_id="u1") as guard:
            stream = client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=100,
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
            for _ in stream:
                pass

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    async def test_async_streaming_records_usage(self, anthropic_mocks):
        import anthropic

        _, async_mock = anthropic_mocks
        async_mock.return_value = _AsyncIter(
            [self._event_start(100), self._event_delta(50)]
        )

        client = anthropic.AsyncAnthropic(api_key="test")
        async with BudgetGuard(user_id="u1") as guard:
            stream = await client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=100,
                messages=[{"role": "user", "content": "hi"}],
                stream=True,
            )
            async for _ in stream:
                pass

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_non_messages_endpoint_is_ignored(self, anthropic_mocks):
        from anthropic._base_client import SyncAPIClient
        from anthropic._models import FinalRequestOptions
        from actguard.core.state import BudgetState, reset_state, set_state

        sync_mock, _ = anthropic_mocks
        sync_mock.return_value = self._resp(100, 50)

        _ant_mod.patch_anthropic()
        client = SyncAPIClient(
            version="0.83.0",
            base_url="https://api.anthropic.com",
            max_retries=0,
            timeout=10.0,
            _strict_response_validation=False,
        )

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=None)
        token = set_state(state)
        try:
            opts = FinalRequestOptions.construct(
                method="post",
                url="/v1/models",
                json_data={"model": "claude-3-haiku-20240307"},
            )
            client.request(cast_to=object, options=opts)
        finally:
            reset_state(token)

        assert state.tokens_used == 0
        assert state.usd_used == 0.0

    def test_token_limit_exceeded(self, anthropic_mocks):
        import anthropic

        sync_mock, _ = anthropic_mocks
        sync_mock.return_value = self._resp(200, 0)

        client = anthropic.Anthropic(api_key="test")
        with pytest.raises(BudgetExceededError) as exc_info:
            with BudgetGuard(user_id="u1", token_limit=100):
                client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=100,
                    messages=[{"role": "user", "content": "hi"}],
                )
        assert exc_info.value.limit_type == "token"

    def test_usd_limit_exceeded(self, anthropic_mocks):
        import anthropic

        sync_mock, _ = anthropic_mocks
        sync_mock.return_value = self._resp(1_000_000, 0)

        client = anthropic.Anthropic(api_key="test")
        with pytest.raises(BudgetExceededError) as exc_info:
            with BudgetGuard(user_id="u1", usd_limit=0.1):
                client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=100,
                    messages=[{"role": "user", "content": "hi"}],
                )
        assert exc_info.value.limit_type == "usd"

    def test_patch_anthropic_is_idempotent(self, anthropic_mocks):
        from anthropic._base_client import SyncAPIClient

        _ant_mod.patch_anthropic()
        first = SyncAPIClient.request
        _ant_mod.patch_anthropic()
        second = SyncAPIClient.request
        assert first is second


# ---------------------------------------------------------------------------
# Google (google-genai) low-level patch tests
# ---------------------------------------------------------------------------

@pytest.fixture
def google_genai_stubs(monkeypatch):
    import actguard.integrations.google as _g_mod

    class BaseApiClient:
        def __init__(self):
            self.sync_result = None
            self.sync_stream_items = []
            self.async_result = None
            self.async_stream_items = []

        def request(self, *args, **kwargs):
            return self.sync_result

        def request_streamed(self, *args, **kwargs):
            return iter(self.sync_stream_items)

        async def async_request(self, *args, **kwargs):
            return self.async_result

        async def async_request_streamed(self, *args, **kwargs):
            return _AsyncIter(self.async_stream_items)

    old_patched = _g_mod._patched
    old_modules = {name: sys.modules.get(name) for name in ("google", "google.genai", "google.genai._api_client")}

    google_pkg = ModuleType("google")
    genai_pkg = ModuleType("google.genai")
    api_client_mod = ModuleType("google.genai._api_client")
    genai_pkg.__version__ = "0.8.0"
    api_client_mod.BaseApiClient = BaseApiClient
    google_pkg.genai = genai_pkg
    genai_pkg._api_client = api_client_mod

    sys.modules["google"] = google_pkg
    sys.modules["google.genai"] = genai_pkg
    sys.modules["google.genai._api_client"] = api_client_mod

    monkeypatch.setattr(_g_mod.importlib.util, "find_spec", lambda name: object() if name == "google.genai" else None)
    _g_mod._patched = False

    yield BaseApiClient, _g_mod

    _g_mod._patched = old_patched
    for name, value in old_modules.items():
        if value is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = value


class TestGoogleGenAIPatch:
    _EXPECTED_COST = (100 * 0.10 + 50 * 0.40) / 1_000_000

    def test_sync_non_streaming_records_usage(self, google_genai_stubs):
        from actguard.core.state import BudgetState, reset_state, set_state

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(
            body=json.dumps(
                {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=None)
        token = set_state(state)
        try:
            client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})
        finally:
            reset_state(token)

        assert state.tokens_used == 150
        assert state.usd_used == pytest.approx(self._EXPECTED_COST)

    async def test_async_non_streaming_records_usage(self, google_genai_stubs):
        from actguard.core.state import BudgetState, reset_state, set_state

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.async_result = SimpleNamespace(
            body=json.dumps(
                {"usage_metadata": {"prompt_token_count": 100, "candidates_token_count": 50}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=None)
        token = set_state(state)
        try:
            await client.async_request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})
        finally:
            reset_state(token)

        assert state.tokens_used == 150
        assert state.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_sync_streaming_records_latest_usage_once(self, google_genai_stubs):
        from actguard.core.state import BudgetState, reset_state, set_state

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_stream_items = [
            SimpleNamespace(body=json.dumps({"text": "partial"}), headers={}),
            SimpleNamespace(
                body=json.dumps(
                    {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10}}
                ),
                headers={},
            ),
            SimpleNamespace(
                body=json.dumps(
                    {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}}
                ),
                headers={},
            ),
        ]

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=None)
        token = set_state(state)
        try:
            for _ in client.request_streamed(
                "post",
                "/v1beta/publishers/google/models/gemini-2.0-flash:streamGenerateContent",
                {},
            ):
                pass
        finally:
            reset_state(token)

        assert state.tokens_used == 150
        assert state.usd_used == pytest.approx(self._EXPECTED_COST)

    async def test_async_streaming_records_latest_usage_once(self, google_genai_stubs):
        from actguard.core.state import BudgetState, reset_state, set_state

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.async_stream_items = [
            SimpleNamespace(body=json.dumps({"text": "partial"}), headers={}),
            SimpleNamespace(
                body=json.dumps(
                    {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10}}
                ),
                headers={},
            ),
            SimpleNamespace(
                body=json.dumps(
                    {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}}
                ),
                headers={},
            ),
        ]

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=None)
        token = set_state(state)
        try:
            stream = await client.async_request_streamed(
                "post",
                "/v1beta/models/gemini-2.0-flash:streamGenerateContent",
                {},
            )
            async for _ in stream:
                pass
        finally:
            reset_state(token)

        assert state.tokens_used == 150
        assert state.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_non_generate_endpoint_not_recorded(self, google_genai_stubs):
        from actguard.core.state import BudgetState, reset_state, set_state

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(
            body=json.dumps(
                {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=None)
        token = set_state(state)
        try:
            client.request("post", "/v1beta/models/gemini-2.0-flash:countTokens", {})
        finally:
            reset_state(token)

        assert state.tokens_used == 0
        assert state.usd_used == 0.0

    def test_missing_usage_does_not_record(self, google_genai_stubs):
        from actguard.core.state import BudgetState, reset_state, set_state

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(body=json.dumps({"no_usage": True}), headers={})

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=None)
        token = set_state(state)
        try:
            client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})
        finally:
            reset_state(token)

        assert state.tokens_used == 0
        assert state.usd_used == 0.0

    def test_invalid_json_body_does_not_record(self, google_genai_stubs):
        from actguard.core.state import BudgetState, reset_state, set_state

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(body="{not-json", headers={})

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=None)
        token = set_state(state)
        try:
            client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})
        finally:
            reset_state(token)

        assert state.tokens_used == 0
        assert state.usd_used == 0.0

    def test_token_limit_exceeded(self, google_genai_stubs):
        from actguard.core.state import BudgetState, reset_state, set_state

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(
            body=json.dumps(
                {"usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 0}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", token_limit=100, usd_limit=None)
        token = set_state(state)
        try:
            with pytest.raises(BudgetExceededError) as exc_info:
                client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})
        finally:
            reset_state(token)

        assert exc_info.value.limit_type == "token"

    def test_usd_limit_exceeded(self, google_genai_stubs):
        from actguard.core.state import BudgetState, reset_state, set_state

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(
            body=json.dumps(
                {"usageMetadata": {"promptTokenCount": 1_000_000, "candidatesTokenCount": 0}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", token_limit=None, usd_limit=0.09)
        token = set_state(state)
        try:
            with pytest.raises(BudgetExceededError) as exc_info:
                client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})
        finally:
            reset_state(token)

        assert exc_info.value.limit_type == "usd"

    def test_patch_google_is_idempotent(self, google_genai_stubs):
        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()
        first_request = BaseApiClient.request

        google_mod.patch_google()
        second_request = BaseApiClient.request

        assert first_request is second_request

    def test_model_extraction_from_publishers_path(self, google_genai_stubs):
        _, google_mod = google_genai_stubs
        model = google_mod._model_from_path(
            "/v1beta/publishers/google/models/gemini-2.0-flash:generateContent"
        )
        assert model == "gemini-2.0-flash"

    def test_model_extraction_from_short_path(self, google_genai_stubs):
        _, google_mod = google_genai_stubs
        model = google_mod._model_from_path("gemini-2.0-flash:generateContent")
        assert model == "gemini-2.0-flash"
