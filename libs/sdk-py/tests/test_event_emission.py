"""Tests for the event emission pipeline via emit_violation()."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import actguard
import actguard.integrations.openai as _oai_mod
from actguard.core.budget_context import get_budget_state
from actguard.events.envelope import ActGuardContextEvidenceProvider
from actguard.exceptions import BudgetExceededError
from actguard.reporting import record_response_usage


@pytest.fixture()
def client(monkeypatch):
    runtime_client = actguard.Client(
        gateway_url="http://localhost:9999",
        api_key="test-key",
    )
    assert runtime_client.event_client is not None
    monkeypatch.setattr(
        runtime_client.event_client,
        "_ship_with_retry",
        lambda batch: None,
    )
    try:
        yield runtime_client
    finally:
        runtime_client.close()


@pytest.fixture()
def emitted_events(client, monkeypatch):
    assert client.event_client is not None
    captured = []

    def _enqueue(envelope):
        captured.append(envelope)
        return True

    monkeypatch.setattr(client.event_client, "enqueue", _enqueue)
    return captured


@pytest.fixture()
def stub_budget_transport(client, monkeypatch):
    monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-event")
    monkeypatch.setattr(client, "settle_budget", lambda **_: None)


@pytest.fixture()
def openai_usage_mock():
    from openai._base_client import SyncAPIClient

    orig_request = SyncAPIClient.request
    orig_patched = _oai_mod._patched

    sync_mock = MagicMock()
    SyncAPIClient.request = sync_mock
    _oai_mod._patched = False

    yield sync_mock

    SyncAPIClient.request = orig_request
    _oai_mod._patched = orig_patched


def test_budget_exceeded_emits_violation_event(
    client, emitted_events, stub_budget_transport
):
    error = BudgetExceededError(
        user_id="alice",
        tokens_used=1000,
        usd_used=0.05,
        usd_limit=None,
        limit_type="usd",
    )

    with client.run(user_id="alice"):
        with client.budget_guard(user_id="alice", usd_limit=0.05):
            actguard.emit_violation(error)

    matches = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name == "limit_exceeded"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    assert envelope.category == "budget"
    assert envelope.name == "limit_exceeded"
    assert envelope.severity == "error"
    assert envelope.outcome == "blocked"
    assert envelope.payload["user_id"] == "alice"
    assert envelope.payload["tokens_used"] == 1000
    assert envelope.payload["limit_type"] == "usd"


def test_budget_guard_events_include_run_id(
    client, emitted_events, stub_budget_transport
):
    with client.run(user_id="alice", run_id="run-ctx"):
        with client.budget_guard(user_id="alice", usd_limit=0.05) as guard:
            actguard.emit_event("tool", "invoke", {})

    matches = [
        env
        for env in emitted_events
        if env.category == "tool" and env.name == "invoke"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    assert envelope.run_id == "run-ctx"
    assert guard.run_id == envelope.run_id


def test_budget_lifecycle_events_are_not_emitted_by_sdk(
    client, emitted_events, stub_budget_transport
):
    with client.run(run_id="budget-lifecycle"):
        with client.budget_guard(run_id="budget-lifecycle", usd_limit=0.05):
            pass

    lifecycle = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name in {"released", "reserved", "settled"}
    ]
    assert lifecycle == []


def test_nested_budget_events_include_top_level_scope_metadata(
    client, emitted_events, stub_budget_transport
):
    with client.run(run_id="run-shared"):
        with client.budget_guard(user_id="outer", usd_limit=0.2, plan_key="pro"):
            with client.budget_guard(name="search_tool", usd_limit=0.1):
                actguard.emit_event("tool", "invoke", {"tool_name": "search"})

    matches = [
        env
        for env in emitted_events
        if env.category == "tool" and env.name == "invoke"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    wire = envelope.to_dict()
    assert envelope.scope_kind == "nested"
    assert envelope.scope_name == "search_tool"
    assert envelope.scope_id
    assert envelope.parent_scope_id
    assert envelope.root_scope_id
    assert wire["scope_kind"] == "nested"
    assert wire["scope_name"] == "search_tool"
    assert wire["scope_id"] == envelope.scope_id
    assert wire["parent_scope_id"] == envelope.parent_scope_id
    assert wire["root_scope_id"] == envelope.root_scope_id
    assert envelope.plan_key == "pro"
    assert wire["plan_key"] == "pro"


def test_emit_violation_no_op_without_config():
    error = BudgetExceededError(
        user_id="alice",
        tokens_used=100,
        usd_used=0.01,
        usd_limit=None,
        limit_type="usd",
    )
    actguard.emit_violation(error)


def test_user_id_none_stays_none_in_emitted_envelope(client, emitted_events):
    with client.run(run_id="run-none-user"):
        actguard.emit_event("tool", "invoke", {})

    matches = [
        env
        for env in emitted_events
        if env.category == "tool" and env.name == "invoke"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    assert envelope.user_id is None
    assert "user_id" not in envelope.to_dict()


def test_context_evidence_provider_reads_active_run_state(client):
    with client.run(run_id="run-evidence", user_id="alice"):
        evidence = ActGuardContextEvidenceProvider().current()

    assert len(evidence) == 1
    assert evidence[0].attrs == {"run_id": "run-evidence", "user_id": "alice"}


def test_emit_event_populates_top_level_usage_fields(client, emitted_events):
    with client.run(run_id="run-usage-fields", user_id="alice"):
        actguard.emit_event(
            "tool",
            "invoke",
            {
                "model": "payload-model",
                "input_tokens": 1,
                "cached_input_tokens": 2,
                "output_tokens": 3,
                "provider": "payload-provider",
                "tool_name": "payload-tool",
            },
            provider="openai",
            model="gpt-4o-mini",
            usd_micros=420_000,
            input_tokens=931,
            cached_input_tokens=7,
            output_tokens=30,
            tool_name="top-level-tool",
        )

    matches = [
        env
        for env in emitted_events
        if env.category == "tool" and env.name == "invoke"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    wire = envelope.to_dict()
    assert envelope.model == "gpt-4o-mini"
    assert envelope.provider == "openai"
    assert envelope.tool_name == "top-level-tool"
    assert envelope.usd_micros == 420_000
    assert envelope.input_tokens == 931
    assert envelope.cached_input_tokens == 7
    assert envelope.output_tokens == 30
    assert wire["model"] == "gpt-4o-mini"
    assert wire["provider"] == "openai"
    assert wire["tool_name"] == "top-level-tool"
    assert wire["usd_micros"] == 420_000
    assert wire["input_tokens"] == 931
    assert wire["cached_input_tokens"] == 7
    assert wire["output_tokens"] == 30
    assert envelope.payload["input_tokens"] == 1
    assert envelope.payload["cached_input_tokens"] == 2


def test_emit_event_uses_snake_case_wire_keys(client, emitted_events):
    with client.run(run_id="run-wire-shape", user_id="alice"):
        actguard.emit_event("tool", "invoke", {})

    matches = [
        env
        for env in emitted_events
        if env.category == "tool" and env.name == "invoke"
    ]
    assert len(matches) == 1
    wire = matches[0].to_dict()
    assert "run_id" in wire
    assert "user_id" in wire
    assert "tenant_id" in wire
    assert "ingested_at" in wire
    assert "digest_algo" in wire
    assert "runID" not in wire
    assert "userID" not in wire
    assert "tenantID" not in wire
    assert "ingestedAt" not in wire
    assert "digestAlgo" not in wire


def test_emit_event_promotes_usage_fields_from_payload(client, emitted_events):
    with client.run(run_id="run-usage-from-payload"):
        actguard.emit_event(
            "tool",
            "invoke",
            {
                "model": "gpt-4o-mini",
                "provider": "openai",
                "usd_micros": 123_456,
                "input_tokens": 22,
                "cached_input_tokens": 4,
                "output_tokens": 6,
                "tool_name": "search",
            },
        )

    matches = [
        env
        for env in emitted_events
        if env.category == "tool" and env.name == "invoke"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    wire = envelope.to_dict()
    assert envelope.model == "gpt-4o-mini"
    assert envelope.provider == "openai"
    assert envelope.tool_name == "search"
    assert envelope.usd_micros == 123_456
    assert envelope.input_tokens == 22
    assert envelope.cached_input_tokens == 4
    assert envelope.output_tokens == 6
    assert wire["model"] == "gpt-4o-mini"
    assert wire["provider"] == "openai"
    assert wire["tool_name"] == "search"
    assert wire["usd_micros"] == 123_456


def test_emit_event_promotes_langchain_openai_usage_from_raw_payload(
    client, emitted_events
):
    raw = SimpleNamespace(
        usage_metadata={
            "input_tokens": 31,
            "output_tokens": 5,
            "input_token_details": {"cache_read": 7},
        },
        response_metadata={
            "model_name": "gpt-4o",
            "token_usage": {"prompt_tokens": 31, "completion_tokens": 5},
        },
    )

    with client.run(run_id="run-usage-from-raw"):
        actguard.emit_event("tool", "invoke", {"raw": raw})

    matches = [
        env
        for env in emitted_events
        if env.category == "tool" and env.name == "invoke"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    assert envelope.provider == "openai"
    assert envelope.model == "gpt-4o"
    assert envelope.input_tokens == 31
    assert envelope.cached_input_tokens == 7
    assert envelope.output_tokens == 5


def test_emit_event_omits_top_level_usage_fields_when_unknown(client, emitted_events):
    with client.run(run_id="run-usage-unknown"):
        actguard.emit_event("tool", "invoke", {"tokens_used": 0})

    matches = [
        env
        for env in emitted_events
        if env.category == "tool" and env.name == "invoke"
    ]
    assert len(matches) == 1
    wire = matches[0].to_dict()
    assert "model" not in wire
    assert "provider" not in wire
    assert "usd_micros" not in wire
    assert "input_tokens" not in wire
    assert "cached_input_tokens" not in wire
    assert "output_tokens" not in wire


def test_no_event_emission_without_active_runtime_context(client, emitted_events):
    actguard.emit_event("tool", "invoke", {})

    matches = [
        env
        for env in emitted_events
        if env.category == "tool" and env.name == "invoke"
    ]
    assert matches == []


def test_record_response_usage_records_openai_langchain_message(
    client, emitted_events, stub_budget_transport
):
    raw = SimpleNamespace(
        usage_metadata={
            "input_tokens": 31,
            "output_tokens": 5,
            "input_token_details": {"cache_read": 7},
        },
        response_metadata={
            "model_name": "gpt-4o",
            "token_usage": {"prompt_tokens": 31, "completion_tokens": 5},
        },
    )

    with client.run(run_id="run-openai-wrapper", user_id="alice"):
        with client.budget_guard(name="search_tool", usd_limit=1.0):
            assert record_response_usage({"raw": raw}, provider="openai")
            state = get_budget_state()
            assert state is not None
            assert state.provider_model_id == "gpt-4o"
            assert state.input_tokens == 31
            assert state.cached_input_tokens == 7
            assert state.output_tokens == 5

    matches = [
        env
        for env in emitted_events
        if env.category == "llm" and env.name == "usage"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    assert envelope.provider == "openai"
    assert envelope.model == "gpt-4o"
    assert envelope.input_tokens == 31
    assert envelope.cached_input_tokens == 7
    assert envelope.output_tokens == 5


def test_record_response_usage_infers_anthropic_provider_from_model(
    client, emitted_events, stub_budget_transport
):
    raw = SimpleNamespace(
        usage_metadata={"input_tokens": 11, "output_tokens": 7},
        response_metadata={
            "model": "claude-3-5-sonnet-latest",
            "usage": {"input_tokens": 11, "output_tokens": 7},
        },
    )

    with client.run(run_id="run-anthropic-wrapper", user_id="alice"):
        with client.budget_guard(name="search_tool", usd_limit=1.0):
            assert record_response_usage(raw)

    matches = [
        env
        for env in emitted_events
        if env.category == "llm" and env.name == "usage"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    assert envelope.provider == "anthropic"
    assert envelope.model == "claude-3-5-sonnet-latest"
    assert envelope.input_tokens == 11
    assert envelope.output_tokens == 7


def test_record_response_usage_infers_google_provider_from_response_metadata(
    client, emitted_events, stub_budget_transport
):
    raw = SimpleNamespace(
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 24,
            "input_token_details": {"cache_read": 0},
        },
        response_metadata={
            "model_name": "gemini-3.1-pro-preview",
            "model_provider": "google_genai",
        },
    )

    with client.run(run_id="run-google-wrapper", user_id="alice"):
        with client.budget_guard(name="search_tool", usd_limit=1.0):
            assert record_response_usage(raw)

    matches = [
        env
        for env in emitted_events
        if env.category == "llm" and env.name == "usage"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    assert envelope.provider == "google"
    assert envelope.model == "gemini-3.1-pro-preview"
    assert envelope.input_tokens == 10
    assert envelope.output_tokens == 24


def test_record_response_usage_returns_false_when_usage_missing(
    client, emitted_events, stub_budget_transport
):
    raw = SimpleNamespace(response_metadata={"model_name": "gpt-4o"})

    with client.run(run_id="run-no-usage", user_id="alice"):
        with client.budget_guard(name="search_tool", usd_limit=1.0):
            assert not record_response_usage(raw, provider="openai")

    matches = [
        env
        for env in emitted_events
        if env.category == "llm" and env.name == "usage"
    ]
    assert matches == []


def test_no_event_leakage_between_multiple_clients(monkeypatch):
    client_a = actguard.Client(gateway_url="http://localhost:9999", api_key="key-a")
    client_b = actguard.Client(gateway_url="http://localhost:9999", api_key="key-b")
    assert client_a.event_client is not None
    assert client_b.event_client is not None

    events_a = []
    events_b = []

    monkeypatch.setattr(
        client_a.event_client,
        "enqueue",
        lambda envelope: events_a.append(envelope) or True,
    )
    monkeypatch.setattr(
        client_b.event_client,
        "enqueue",
        lambda envelope: events_b.append(envelope) or True,
    )

    with client_a.run(run_id="run-a"):
        actguard.emit_event("tool", "invoke", {})
    with client_b.run(run_id="run-b"):
        actguard.emit_event("tool", "invoke", {})

    a_budget = [e for e in events_a if e.category == "tool" and e.name == "invoke"]
    b_budget = [e for e in events_b if e.category == "tool" and e.name == "invoke"]
    assert len(a_budget) == 1
    assert len(b_budget) == 1
    assert a_budget[0].run_id == "run-a"
    assert b_budget[0].run_id == "run-b"

    client_a.close()
    client_b.close()


def test_openai_provider_call_emits_one_llm_usage_event(
    client,
    emitted_events,
    stub_budget_transport,
    openai_usage_mock,
):
    import openai

    openai_usage_mock.return_value = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50)
    )
    model_client = openai.OpenAI(api_key="sk-test")

    @actguard.tool()
    def call_model():
        return model_client.chat.completions.create(model="gpt-4o", messages=[])

    with client.run(run_id="run-llm-usage", user_id="alice"):
        with client.budget_guard(name="search_tool", usd_limit=1.0):
            call_model()

    matches = [
        env
        for env in emitted_events
        if env.category == "llm" and env.name == "usage"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    wire = envelope.to_dict()
    assert envelope.provider == "openai"
    assert envelope.model == "gpt-4o"
    assert envelope.scope_name == "search_tool"
    assert envelope.tool_name
    assert envelope.input_tokens == 100
    assert envelope.output_tokens == 50
    assert envelope.usd_micros is None
    assert wire["provider"] == "openai"
    assert wire["scope_name"] == "search_tool"
    assert wire["tool_name"] == envelope.tool_name


def test_only_llm_usage_event_is_eligible_for_attributed_spend(client, emitted_events):
    from actguard.reporting_contract import is_attributed_spend_event

    with client.run(run_id="run-contract"):
        actguard.emit_event("tool", "invoke", {"usd_micros": 123})
        actguard.emit_event("llm", "usage", {}, provider="openai", usd_micros=456)

    matches = [
        env
        for env in emitted_events
        if is_attributed_spend_event(
            category=env.category,
            name=env.name,
            usd_micros=env.usd_micros,
        )
    ]
    assert len(matches) == 1
    assert matches[0].category == "llm"
    assert matches[0].name == "usage"


def test_run_success_emits_start_and_end_success(client, emitted_events):
    with client.run(run_id="run-success", user_id="alice"):
        pass

    starts = [e for e in emitted_events if e.category == "run" and e.name == "start"]
    ends = [e for e in emitted_events if e.category == "run" and e.name == "end"]
    assert len(starts) == 1
    assert len(ends) == 1
    assert starts[0].run_id == "run-success"
    assert ends[0].run_id == "run-success"
    assert ends[0].outcome == "success"


def test_run_blocked_emits_end_blocked(client, emitted_events):
    violation = BudgetExceededError(
        user_id="alice",
        tokens_used=1000,
        usd_used=0.05,
        usd_limit=None,
        limit_type="usd",
    )

    with pytest.raises(BudgetExceededError):
        with client.run(run_id="run-blocked", user_id="alice"):
            raise violation

    starts = [e for e in emitted_events if e.category == "run" and e.name == "start"]
    ends = [e for e in emitted_events if e.category == "run" and e.name == "end"]
    assert len(starts) == 1
    assert len(ends) == 1
    assert ends[0].outcome == "blocked"
    assert ends[0].payload["error_type"] == "BudgetExceededError"


def test_run_failed_emits_end_failed(client, emitted_events):
    with pytest.raises(RuntimeError, match="boom"):
        with client.run(run_id="run-failed"):
            raise RuntimeError("boom")

    starts = [e for e in emitted_events if e.category == "run" and e.name == "start"]
    ends = [e for e in emitted_events if e.category == "run" and e.name == "end"]
    assert len(starts) == 1
    assert len(ends) == 1
    assert ends[0].outcome == "failed"
    assert ends[0].payload["error_type"] == "RuntimeError"
