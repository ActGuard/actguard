"""Tests for BudgetGuard core behaviour (no real LLM calls)."""
from contextlib import asynccontextmanager, contextmanager
import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import actguard
import actguard.integrations.anthropic as _ant_mod
import actguard.integrations.openai as _oai_mod
from actguard.core.budget_context import (
    _active_budget_contexts,
    _active_budget_states,
    _active_budget_states_lock,
    add_cost,
    check_budget_limits,
    get_budget_state,
    get_budget_stack,
    record_usage,
    reset_budget_state,
    set_budget_state,
)
from actguard.core.pricing import get_cost
from actguard.core.state import get_current_state
from actguard.exceptions import (
    BudgetConfigurationError,
    BudgetExceededError,
    MissingRuntimeContextError,
)


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


@contextmanager
def _with_runtime_budget_state(state):
    client = actguard.Client()
    with client.run(run_id="test-budget-runtime"):
        token = set_budget_state(state)
        try:
            yield
        finally:
            reset_budget_state(token)
    client.close()


@contextmanager
def _with_runtime_budget_guard(
    *,
    user_id=None,
    name=None,
    usd_limit: float | None,
    run_id=None,
    client=None,
):
    runtime_client = client or actguard.Client()
    try:
        with runtime_client.run(user_id=user_id, run_id=run_id):
            with runtime_client.budget_guard(
                user_id=user_id,
                name=name,
                usd_limit=usd_limit,
                run_id=run_id,
            ) as guard:
                yield guard
    finally:
        if client is None:
            runtime_client.close()


@asynccontextmanager
async def _with_runtime_budget_guard_async(
    *,
    user_id=None,
    name=None,
    usd_limit: float | None,
    run_id=None,
    client=None,
):
    runtime_client = client or actguard.Client()
    try:
        async with runtime_client.run(user_id=user_id, run_id=run_id):
            async with runtime_client.budget_guard(
                user_id=user_id,
                name=name,
                usd_limit=usd_limit,
                run_id=run_id,
            ) as guard:
                yield guard
    finally:
        if client is None:
            runtime_client.close()


# ---------------------------------------------------------------------------
# OpenAI mock fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def openai_mocks():
    from openai._base_client import AsyncAPIClient, SyncAPIClient

    # Save original state
    orig_request = SyncAPIClient.request
    orig_async_request = AsyncAPIClient.request
    orig_patched = _oai_mod._patched

    # Install mocks so patch_openai() captures them as _orig_request
    sync_mock = MagicMock()
    async_mock = AsyncMock()
    SyncAPIClient.request = sync_mock
    AsyncAPIClient.request = async_mock

    # Reset so patch_openai() will run on the next budget_guard.__enter__()
    _oai_mod._patched = False

    yield sync_mock, async_mock

    # Restore original state
    SyncAPIClient.request = orig_request
    AsyncAPIClient.request = orig_async_request
    _oai_mod._patched = orig_patched


@pytest.fixture
def anthropic_mocks():
    from anthropic._base_client import AsyncAPIClient, SyncAPIClient

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


@pytest.fixture(autouse=True)
def clear_budget_registries():
    with _active_budget_states_lock:
        _active_budget_states.clear()
        _active_budget_contexts.clear()
    yield
    with _active_budget_states_lock:
        _active_budget_states.clear()
        _active_budget_contexts.clear()


@pytest.fixture(autouse=True)
def stub_budget_transport(monkeypatch):
    def reserve_budget(self, *, run_id, usd_limit_micros, plan_key=None, user_id=None):
        return "res-test"

    def settle_budget(
        self,
        *,
        reserve_id,
        provider,
        provider_model_id,
        input_tokens,
        cached_input_tokens,
        output_tokens,
    ):
        return None

    monkeypatch.setattr(
        actguard.Client,
        "reserve_budget",
        reserve_budget,
    )
    monkeypatch.setattr(
        actguard.Client,
        "settle_budget",
        settle_budget,
    )


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
        assert get_budget_state() is None
        with _with_runtime_budget_guard(user_id="alice", usd_limit=1.0):
            state = get_current_state()
            assert state is not None
            assert state.user_id == "alice"
            assert get_budget_state() is state
        assert get_current_state() is None
        assert get_budget_state() is None

    def test_budget_guard_requires_active_run(self):
        client = actguard.Client()

        with pytest.raises(MissingRuntimeContextError):
            with client.budget_guard(user_id="alice", usd_limit=1.0):
                pass

    def test_nested_budget_guard_pushes_and_pops_stack(self):
        client = actguard.Client()
        with client.run(run_id="run-budget-nested", user_id="outer"):
            with client.budget_guard(user_id="outer", usd_limit=1.0, plan_key="pro") as root_guard:
                root_state = get_budget_state()
                assert root_state is not None
                assert root_state.scope_kind == "root"
                assert root_state.plan_key == "pro"
                assert len(get_budget_stack()) == 1

                with client.budget_guard(name="search_tool", usd_limit=0.2) as nested_guard:
                    nested_state = get_budget_state()
                    assert nested_state is not None
                    assert nested_state.scope_kind == "nested"
                    assert nested_state.scope_name == "search_tool"
                    assert nested_state.plan_key == "pro"
                    assert nested_state.parent_scope_id == root_state.scope_id
                    assert nested_state.root_scope_id == root_state.root_scope_id
                    assert len(get_budget_stack()) == 2

                restored = get_budget_state()
                assert restored is not None
                assert restored.scope_id == root_state.scope_id
                assert len(get_budget_stack()) == 1
                assert nested_guard.tokens_used == 0
                assert root_guard.tokens_used == 0

    def test_budget_guard_without_limit_is_allowed_for_attribution(self):
        client = actguard.Client()
        with client.run(run_id="run-attribution-only", user_id="alice"):
            with client.budget_guard(name="root") as root_guard:
                root_state = get_budget_state()
                assert root_state is not None
                assert root_state.scope_kind == "root"
                assert root_state.usd_limit is None
                assert root_guard.usd_used == 0.0

                with client.budget_guard(name="reranker"):
                    nested_state = get_budget_state()
                    assert nested_state is not None
                    assert nested_state.scope_kind == "nested"
                    assert nested_state.scope_name == "reranker"
                    assert nested_state.usd_limit is None

    def test_exception_propagated_and_state_cleared(self):
        with pytest.raises(ValueError):
            with _with_runtime_budget_guard(user_id="alice", usd_limit=1.0):
                raise ValueError("boom")
        assert get_current_state() is None

    def test_budget_guard_uses_active_run_id(self):
        with _with_runtime_budget_guard(user_id="alice", usd_limit=1.0) as g:
            assert g.run_id is not None

    def test_run_state_set_and_cleared(self):
        from actguard.core.run_context import get_run_state

        assert get_run_state() is None
        with _with_runtime_budget_guard(user_id="alice", usd_limit=1.0) as g:
            state = get_run_state()
            assert state is not None
            assert state.run_id == g.run_id
            assert not hasattr(state, "budget_state")
        assert get_run_state() is None

    def test_run_context_can_exist_without_budget_state(self):
        from actguard.core.run_context import get_run_state

        client = actguard.Client()
        with client.run(run_id="run-without-budget", user_id="alice"):
            state = get_run_state()
            assert state is not None
            assert state.run_id == "run-without-budget"
            assert state.user_id == "alice"
            assert not hasattr(state, "budget_state")
            assert get_budget_state() is None
            assert get_current_state() is None

    def test_budget_guard_inside_client_run_uses_same_run_id(self):
        from actguard.core.run_context import get_run_state

        client = actguard.Client()
        with client.run(run_id="run-shared", user_id="outer"):
            with client.budget_guard(usd_limit=1.0):
                assert get_current_state() is not None
                assert get_current_state().user_id == "outer"
                assert get_run_state() is not None
                assert get_run_state().run_id == "run-shared"
                assert get_budget_state() is not None
            assert get_run_state() is not None
            assert get_run_state().run_id == "run-shared"
            assert get_budget_state() is None
        assert get_run_state() is None


# ---------------------------------------------------------------------------
# Async context manager
# ---------------------------------------------------------------------------

class TestBudgetGuardAsync:
    pytestmark = pytest.mark.asyncio

    async def test_parallel_async_tasks_keep_independent_nested_scopes(self):
        client = actguard.Client()
        seen = {}

        async with client.run(run_id="run-budget-nested-async", user_id="outer"):
            async with client.budget_guard(user_id="outer", usd_limit=1.0):
                async def worker(label: str) -> None:
                    async with client.budget_guard(name=label, usd_limit=0.3):
                        state = get_budget_state()
                        assert state is not None
                        seen[label] = (
                            state.scope_id,
                            state.parent_scope_id,
                            state.root_scope_id,
                        )
                        await asyncio.sleep(0)

                await asyncio.gather(worker("search_tool"), worker("writer_tool"))

        assert seen["search_tool"][0] != seen["writer_tool"][0]
        assert seen["search_tool"][1] == seen["writer_tool"][1]
        assert seen["search_tool"][2] == seen["writer_tool"][2]

    async def test_root_guard_reports_shared_totals_after_parallel_tasks(self):
        client = actguard.Client()

        async with client.run(run_id="run-budget-parallel", user_id="outer"):
            async with client.budget_guard(user_id="outer", usd_limit=1.0) as root_guard:
                async def worker(label: str):
                    async with client.budget_guard(name=label, usd_limit=0.4) as nested_guard:
                        record_usage(
                            provider="openai",
                            provider_model_id="gpt-4o-mini",
                            input_tokens=10,
                            output_tokens=5,
                        )
                        add_cost(0.25)
                        await asyncio.sleep(0)
                        return nested_guard

                search_guard, writer_guard = await asyncio.gather(
                    worker("search_tool"),
                    worker("writer_tool"),
                )

            assert root_guard.tokens_used == 30
            assert root_guard.usd_used == pytest.approx(0.5)
            assert root_guard.local_tokens_used == 0
            assert root_guard.local_usd_used == pytest.approx(0.0)
            assert root_guard.root_tokens_used == 30
            assert root_guard.root_usd_used == pytest.approx(0.5)
            assert search_guard.tokens_used == 15
            assert search_guard.usd_used == pytest.approx(0.25)
            assert search_guard.local_tokens_used == 15
            assert search_guard.local_usd_used == pytest.approx(0.25)
            assert search_guard.root_tokens_used == 30
            assert search_guard.root_usd_used == pytest.approx(0.5)
            assert writer_guard.tokens_used == 15
            assert writer_guard.usd_used == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Nested scope semantics
# ---------------------------------------------------------------------------

class TestNestedBudgetScopes:
    def test_second_path_attaches_to_shared_root_and_reuses_single_reserve(self, monkeypatch):
        import concurrent.futures

        client = actguard.Client(gateway_url="https://gw.example", api_key="sk-test")
        calls = []

        monkeypatch.setattr(
            client,
            "reserve_budget",
            lambda **kwargs: calls.append(("reserve", kwargs)) or "res-shared-root",
        )
        monkeypatch.setattr(
            client,
            "settle_budget",
            lambda **kwargs: calls.append(("settle", kwargs)) or None,
        )

        with client.run(run_id="run-shared-root", user_id="alice"):
            with client.budget_guard(name="root", usd_limit=0.5, plan_key="pro") as root_guard:
                root_state = get_budget_state()
                assert root_state is not None

                def worker():
                    with client.budget_guard():
                        state = get_budget_state()
                        assert state is not None
                        return (
                            state.scope_kind,
                            state.scope_id,
                            state.root_scope_id,
                            state.parent_scope_id,
                        )

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    scope_kind, scope_id, root_scope_id, parent_scope_id = pool.submit(worker).result()

                assert scope_kind == "root"
                assert scope_id == root_state.scope_id
                assert root_scope_id == root_state.root_scope_id
                assert parent_scope_id is None
                assert root_guard.run_id == "run-shared-root"
                assert root_state.plan_key == "pro"

        assert [name for name, _ in calls] == ["reserve", "settle"]
        assert calls[0][1]["usd_limit_micros"] == 500_000
        assert calls[0][1]["plan_key"] == "pro"
        assert calls[0][1]["user_id"] == "alice"

    def test_nested_plan_key_must_match_existing_root(self, monkeypatch):
        import concurrent.futures

        client = actguard.Client()
        monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-root")
        monkeypatch.setattr(client, "settle_budget", lambda **_: None)

        with client.run(run_id="run-plan-mismatch", user_id="alice"):
            with client.budget_guard(name="root", usd_limit=0.5, plan_key="pro"):
                def worker():
                    with pytest.raises(BudgetConfigurationError):
                        with client.budget_guard(name="child", plan_key="enterprise"):
                            pass

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(worker).result()

    def test_root_guard_reports_shared_totals_after_threaded_work(self, monkeypatch):
        import concurrent.futures

        client = actguard.Client()
        monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-threaded")
        monkeypatch.setattr(client, "settle_budget", lambda **_: None)

        with client.run(run_id="run-threaded-root", user_id="alice"):
            with client.budget_guard(name="root", usd_limit=0.5) as root_guard:
                def worker():
                    with client.budget_guard():
                        record_usage(
                            provider="openai",
                            provider_model_id="gpt-4o-mini",
                            input_tokens=20,
                            output_tokens=10,
                        )
                        add_cost(0.15)

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(worker).result()

            assert root_guard.tokens_used == 30
            assert root_guard.usd_used == pytest.approx(0.15)
            assert root_guard.local_tokens_used == 0
            assert root_guard.local_usd_used == pytest.approx(0.0)
            assert root_guard.root_tokens_used == 30
            assert root_guard.root_usd_used == pytest.approx(0.15)

    def test_second_path_root_parameters_must_match_existing_root(self, monkeypatch):
        import concurrent.futures

        client = actguard.Client()
        monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-root")
        monkeypatch.setattr(client, "settle_budget", lambda **_: None)

        with client.run(run_id="run-root-mismatch", user_id="alice"):
            with client.budget_guard(name="root", usd_limit=0.5):
                def worker():
                    with pytest.raises(BudgetConfigurationError):
                        with client.budget_guard(usd_limit=0.75):
                            pass

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(worker).result()

    def test_nested_parent_and_root_limits_are_walked(self, monkeypatch):
        client = actguard.Client()
        monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-enforce")
        monkeypatch.setattr(client, "settle_budget", lambda **_: None)

        with client.run(run_id="run-limit-walk", user_id="alice"):
            with client.budget_guard(name="root", usd_limit=1.0):
                with client.budget_guard(name="parent", usd_limit=0.4):
                    with client.budget_guard(name="child", usd_limit=0.2):
                        record_usage(
                            provider="openai",
                            provider_model_id="gpt-4o-mini",
                            input_tokens=10,
                            output_tokens=5,
                        )
                        add_cost(0.25)
                        violation = check_budget_limits()
                        assert violation is not None
                        assert violation.blocked_scope.scope_name == "child"

                with client.budget_guard(name="parent", usd_limit=0.4):
                    with client.budget_guard(name="child"):
                        record_usage(
                            provider="openai",
                            provider_model_id="gpt-4o-mini",
                            input_tokens=10,
                            output_tokens=5,
                        )
                        add_cost(0.45)
                        violation = check_budget_limits()
                        assert violation is not None
                        assert violation.blocked_scope.scope_name == "parent"

                with client.budget_guard(name="parent"):
                    record_usage(
                        provider="openai",
                        provider_model_id="gpt-4o-mini",
                        input_tokens=10,
                        output_tokens=5,
                    )
                    add_cost(1.05)
                    violation = check_budget_limits()
                    assert violation is not None
                    assert violation.blocked_scope.scope_kind == "root"

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

    @pytest.mark.asyncio
    async def test_async_stream_manual_anext_without_aiter(self):
        from actguard.core.state import BudgetState
        from actguard.integrations.openai import _WrappedAsyncStream

        state = BudgetState(user_id="u1", usd_limit=None)
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
        with _with_runtime_budget_guard(user_id="u1", usd_limit=1.0) as guard:
            client.chat.completions.create(model="gpt-4o", messages=[])

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    @pytest.mark.asyncio
    async def test_async_non_streaming_records_usage(self, openai_mocks):
        import openai
        _, async_mock = openai_mocks
        async_mock.return_value = _resp(100, 50)

        client = openai.AsyncOpenAI(api_key="sk-test")
        async with _with_runtime_budget_guard_async(user_id="u1", usd_limit=1.0) as guard:
            await client.chat.completions.create(model="gpt-4o", messages=[])

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_sync_streaming_records_usage(self, openai_mocks):
        import openai
        sync_mock, _ = openai_mocks
        sync_mock.return_value = iter([_chunk(content="a"), _chunk(100, 50)])

        client = openai.OpenAI(api_key="sk-test")
        with _with_runtime_budget_guard(user_id="u1", usd_limit=1.0) as guard:
            stream = client.chat.completions.create(
                model="gpt-4o", messages=[], stream=True
            )
            for _ in stream:
                pass

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    @pytest.mark.asyncio
    async def test_async_streaming_records_usage(self, openai_mocks):
        import openai
        _, async_mock = openai_mocks
        async_mock.return_value = _AsyncIter([_chunk(content="a"), _chunk(100, 50)])

        client = openai.AsyncOpenAI(api_key="sk-test")
        async with _with_runtime_budget_guard_async(user_id="u1", usd_limit=1.0) as guard:
            stream = await client.chat.completions.create(
                model="gpt-4o", messages=[], stream=True
            )
            async for _ in stream:
                pass

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_budget_limit_exceeded(self, openai_mocks):
        import openai
        sync_mock, _ = openai_mocks
        sync_mock.return_value = _resp(200, 0)

        client = openai.OpenAI(api_key="sk-test")
        with pytest.raises(BudgetExceededError) as exc_info:
            with _with_runtime_budget_guard(user_id="u1", usd_limit=0.0001):
                client.chat.completions.create(model="gpt-4o", messages=[])
        assert exc_info.value.limit_type == "usd"

    def test_usd_limit_exceeded(self, openai_mocks):
        import openai
        sync_mock, _ = openai_mocks
        sync_mock.return_value = _resp(1_000_000, 0)

        client = openai.OpenAI(api_key="sk-test")
        with pytest.raises(BudgetExceededError) as exc_info:
            with _with_runtime_budget_guard(user_id="u1", usd_limit=1.0):
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
        with _with_runtime_budget_guard(user_id="u1", usd_limit=1.0) as guard:
            client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=100,
                messages=[{"role": "user", "content": "hi"}],
            )

        assert guard.tokens_used == 150
        assert guard.usd_used == pytest.approx(self._EXPECTED_COST)

    @pytest.mark.asyncio
    async def test_async_non_streaming_records_usage(self, anthropic_mocks):
        import anthropic

        _, async_mock = anthropic_mocks
        async_mock.return_value = self._resp(100, 50)

        client = anthropic.AsyncAnthropic(api_key="test")
        async with _with_runtime_budget_guard_async(user_id="u1", usd_limit=1.0) as guard:
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
        with _with_runtime_budget_guard(user_id="u1", usd_limit=1.0) as guard:
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

    @pytest.mark.asyncio
    async def test_async_streaming_records_usage(self, anthropic_mocks):
        import anthropic

        _, async_mock = anthropic_mocks
        async_mock.return_value = _AsyncIter(
            [self._event_start(100), self._event_delta(50)]
        )

        client = anthropic.AsyncAnthropic(api_key="test")
        async with _with_runtime_budget_guard_async(user_id="u1", usd_limit=1.0) as guard:
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
        from anthropic._models import FinalRequestOptions
        from anthropic._base_client import SyncAPIClient
        from actguard.core.state import BudgetState

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

        state = BudgetState(user_id="u1", usd_limit=None)
        with _with_runtime_budget_state(state):
            opts = FinalRequestOptions.construct(
                method="post",
                url="/v1/models",
                json_data={"model": "claude-3-haiku-20240307"},
            )
            client.request(cast_to=object, options=opts)

        assert state.tokens_used == 0
        assert state.usd_used == 0.0

    def test_budget_limit_exceeded(self, anthropic_mocks):
        import anthropic

        sync_mock, _ = anthropic_mocks
        sync_mock.return_value = self._resp(200, 0)

        client = anthropic.Anthropic(api_key="test")
        with pytest.raises(BudgetExceededError) as exc_info:
            with _with_runtime_budget_guard(user_id="u1", usd_limit=0.00001):
                client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=100,
                    messages=[{"role": "user", "content": "hi"}],
                )
        assert exc_info.value.limit_type == "usd"

    def test_usd_limit_exceeded(self, anthropic_mocks):
        import anthropic

        sync_mock, _ = anthropic_mocks
        sync_mock.return_value = self._resp(1_000_000, 0)

        client = anthropic.Anthropic(api_key="test")
        with pytest.raises(BudgetExceededError) as exc_info:
            with _with_runtime_budget_guard(user_id="u1", usd_limit=0.1):
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
        from actguard.core.state import BudgetState

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(
            body=json.dumps(
                {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", usd_limit=None)
        with _with_runtime_budget_state(state):
            client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})

        assert state.tokens_used == 150
        assert state.usd_used == pytest.approx(self._EXPECTED_COST)

    @pytest.mark.asyncio
    async def test_async_non_streaming_records_usage(self, google_genai_stubs):
        from actguard.core.state import BudgetState

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.async_result = SimpleNamespace(
            body=json.dumps(
                {"usage_metadata": {"prompt_token_count": 100, "candidates_token_count": 50}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", usd_limit=None)
        with _with_runtime_budget_state(state):
            await client.async_request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})

        assert state.tokens_used == 150
        assert state.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_sync_streaming_records_latest_usage_once(self, google_genai_stubs):
        from actguard.core.state import BudgetState

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

        state = BudgetState(user_id="u1", usd_limit=None)
        with _with_runtime_budget_state(state):
            for _ in client.request_streamed(
                "post",
                "/v1beta/publishers/google/models/gemini-2.0-flash:streamGenerateContent",
                {},
            ):
                pass

        assert state.tokens_used == 150
        assert state.usd_used == pytest.approx(self._EXPECTED_COST)

    @pytest.mark.asyncio
    async def test_async_streaming_records_latest_usage_once(self, google_genai_stubs):
        from actguard.core.state import BudgetState

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

        state = BudgetState(user_id="u1", usd_limit=None)
        with _with_runtime_budget_state(state):
            stream = await client.async_request_streamed(
                "post",
                "/v1beta/models/gemini-2.0-flash:streamGenerateContent",
                {},
            )
            async for _ in stream:
                pass

        assert state.tokens_used == 150
        assert state.usd_used == pytest.approx(self._EXPECTED_COST)

    def test_non_generate_endpoint_not_recorded(self, google_genai_stubs):
        from actguard.core.state import BudgetState

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(
            body=json.dumps(
                {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", usd_limit=None)
        with _with_runtime_budget_state(state):
            client.request("post", "/v1beta/models/gemini-2.0-flash:countTokens", {})

        assert state.tokens_used == 0
        assert state.usd_used == 0.0

    def test_missing_usage_does_not_record(self, google_genai_stubs):
        from actguard.core.state import BudgetState

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(body=json.dumps({"no_usage": True}), headers={})

        state = BudgetState(user_id="u1", usd_limit=None)
        with _with_runtime_budget_state(state):
            client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})

        assert state.tokens_used == 0
        assert state.usd_used == 0.0

    def test_invalid_json_body_does_not_record(self, google_genai_stubs):
        from actguard.core.state import BudgetState

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(body="{not-json", headers={})

        state = BudgetState(user_id="u1", usd_limit=None)
        with _with_runtime_budget_state(state):
            client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})

        assert state.tokens_used == 0
        assert state.usd_used == 0.0

    def test_budget_limit_exceeded(self, google_genai_stubs):
        from actguard.core.state import BudgetState

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(
            body=json.dumps(
                {"usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 0}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", usd_limit=0.00001)
        with _with_runtime_budget_state(state):
            with pytest.raises(BudgetExceededError) as exc_info:
                client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})

        assert exc_info.value.limit_type == "usd"

    def test_usd_limit_exceeded(self, google_genai_stubs):
        from actguard.core.state import BudgetState

        BaseApiClient, google_mod = google_genai_stubs
        google_mod.patch_google()

        client = BaseApiClient()
        client.sync_result = SimpleNamespace(
            body=json.dumps(
                {"usageMetadata": {"promptTokenCount": 1_000_000, "candidatesTokenCount": 0}}
            ),
            headers={},
        )

        state = BudgetState(user_id="u1", usd_limit=0.09)
        with _with_runtime_budget_state(state):
            with pytest.raises(BudgetExceededError) as exc_info:
                client.request("post", "/v1beta/models/gemini-2.0-flash:generateContent", {})

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


# ---------------------------------------------------------------------------
# Thread-safe fallback registry + concurrent write protection
# ---------------------------------------------------------------------------

class TestThreadFallback:
    def test_worker_thread_gets_budget_state(self):
        """ContextVar doesn't propagate to thread pool workers; fallback should."""
        import concurrent.futures
        from actguard.core.state import BudgetState

        results = []

        def worker():
            return get_current_state()

        with _with_runtime_budget_guard(user_id="alice", usd_limit=1.0):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(worker)
                results.append(fut.result())

        assert results[0] is not None
        assert results[0].user_id == "alice"

    def test_multiple_active_states_returns_none(self):
        """When >1 active state exists, fallback must return None (ambiguous)."""
        from actguard.core.state import BudgetState
        from actguard.core.budget_context import get_budget_state

        state1 = BudgetState(run_id="r1")
        state2 = BudgetState(run_id="r2")
        tok1 = set_budget_state(state1)
        reset_budget_state(tok1)
        # Now state1 should be removed; add both manually
        with _active_budget_states_lock:
            _active_budget_states[id(state1)] = state1
            _active_budget_states[id(state2)] = state2
        try:
            # ContextVar is None (was reset), 2 active → None
            assert get_budget_state() is None
        finally:
            with _active_budget_states_lock:
                _active_budget_states.pop(id(state1), None)
                _active_budget_states.pop(id(state2), None)

    def test_concurrent_record_usage_correct_totals(self):
        """Multiple threads calling record_usage should produce correct totals."""
        import concurrent.futures
        from actguard.core.state import BudgetState

        state = BudgetState(user_id="u1", usd_limit=None)
        n_threads = 10
        calls_per_thread = 100

        def worker():
            for _ in range(calls_per_thread):
                state.record_usage(
                    provider="openai",
                    provider_model_id="gpt-4o",
                    input_tokens=1,
                    output_tokens=1,
                )
                state.add_cost(0.001)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
            futs = [pool.submit(worker) for _ in range(n_threads)]
            for f in futs:
                f.result()

        total = n_threads * calls_per_thread
        assert state.input_tokens == total
        assert state.output_tokens == total
        assert state.tokens_used == total * 2
        assert state.usd_used == pytest.approx(total * 0.001)

    def test_fallback_cleaned_up_after_exit(self):
        """After budget_guard exits, worker threads should not see the state."""
        import concurrent.futures

        with _with_runtime_budget_guard(user_id="alice", usd_limit=1.0):
            pass  # exit

        with _active_budget_states_lock:
            count = len(_active_budget_states)
        assert count == 0

        def worker():
            return get_current_state()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(worker).result()
        assert result is None
