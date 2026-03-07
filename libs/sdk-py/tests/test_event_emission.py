"""Tests for the event emission pipeline via emit_violation()."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import actguard
from actguard.exceptions import BudgetExceededError, NestedBudgetGuardError


@pytest.fixture()
def client():
    runtime_client = actguard.Client(
        gateway_url="http://localhost:9999",
        api_key="test-key",
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


def test_budget_exceeded_emits_violation_event(client, emitted_events, stub_budget_transport):
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


def test_budget_guard_events_include_run_id(client, emitted_events, stub_budget_transport):
    with client.run(user_id="alice", run_id="run-ctx"):
        with client.budget_guard(user_id="alice", usd_limit=0.05) as guard:
            actguard.emit_event("budget", "check", {})

    matches = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name == "check"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    assert envelope.run_id
    assert envelope.run_id == "run-ctx"
    assert guard.run_id == envelope.run_id


def test_budget_lifecycle_events_are_not_emitted_by_sdk(
    client, emitted_events, stub_budget_transport
):
    with client.budget_guard(run_id="budget-lifecycle", usd_limit=0.05):
        pass

    lifecycle = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name in {"reserved", "settled"}
    ]
    assert lifecycle == []


def test_nested_budget_guards_fail_clearly(client, stub_budget_transport):
    with client.run(run_id="run-shared"):
        with client.budget_guard(user_id="outer", usd_limit=0.2):
            with pytest.raises(NestedBudgetGuardError):
                with client.budget_guard(user_id="inner", usd_limit=0.1):
                    pass


def test_emit_violation_no_op_without_config():
    # No active client/run context means emit_violation is a no-op.
    error = BudgetExceededError(
        user_id="alice",
        tokens_used=100,
        usd_used=0.01,
        usd_limit=None,
        limit_type="usd",
    )
    actguard.emit_violation(error)  # must not raise


def test_user_id_none_stays_none_in_emitted_envelope(client, emitted_events):
    with client.run(run_id="run-none-user"):
        actguard.emit_event("budget", "check", {})

    matches = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name == "check"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    assert envelope.user_id is None
    assert "userID" not in envelope.to_dict()


def test_emit_event_populates_top_level_usage_fields(client, emitted_events):
    with client.run(run_id="run-usage-fields", user_id="alice"):
        actguard.emit_event(
            "budget",
            "consumed",
            {
                "model": "payload-model",
                "input_tokens": 1,
                "cached_input_tokens": 2,
                "output_tokens": 3,
            },
            model="gpt-4o-mini",
            usd_micros=420_000,
            input_tokens=931,
            cached_input_tokens=7,
            output_tokens=30,
        )

    matches = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name == "consumed"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    wire = envelope.to_dict()
    assert envelope.model == "gpt-4o-mini"
    assert envelope.usd_micros == 420_000
    assert envelope.input_tokens == 931
    assert envelope.cached_input_tokens == 7
    assert envelope.output_tokens == 30
    assert wire["model"] == "gpt-4o-mini"
    assert wire["usd_micros"] == 420_000
    assert wire["input_tokens"] == 931
    assert wire["cached_input_tokens"] == 7
    assert wire["output_tokens"] == 30
    assert envelope.payload["input_tokens"] == 1
    assert envelope.payload["cached_input_tokens"] == 2


def test_emit_event_promotes_usage_fields_from_payload(client, emitted_events):
    with client.run(run_id="run-usage-from-payload"):
        actguard.emit_event(
            "budget",
            "consumed",
            {
                "model": "gpt-4o-mini",
                "usd_micros": 123_456,
                "input_tokens": 22,
                "cached_input_tokens": 4,
                "output_tokens": 6,
            },
        )

    matches = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name == "consumed"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    wire = envelope.to_dict()
    assert envelope.model == "gpt-4o-mini"
    assert envelope.usd_micros == 123_456
    assert envelope.input_tokens == 22
    assert envelope.cached_input_tokens == 4
    assert envelope.output_tokens == 6
    assert wire["model"] == "gpt-4o-mini"
    assert wire["usd_micros"] == 123_456
    assert wire["input_tokens"] == 22
    assert wire["cached_input_tokens"] == 4
    assert wire["output_tokens"] == 6


def test_emit_event_omits_top_level_usage_fields_when_unknown(client, emitted_events):
    with client.run(run_id="run-usage-unknown"):
        actguard.emit_event("budget", "check", {"tokens_used": 0})

    matches = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name == "check"
    ]
    assert len(matches) == 1
    wire = matches[0].to_dict()
    assert "model" not in wire
    assert "usd_micros" not in wire
    assert "input_tokens" not in wire
    assert "cached_input_tokens" not in wire
    assert "output_tokens" not in wire


def test_budget_consumed_helper_sets_top_level_cached_input_tokens(client, emitted_events):
    from actguard.reporting import _emit_budget_consumed

    state = SimpleNamespace(
        user_id="alice",
        tokens_used=12,
        usd_used=0.000321,
    )
    with client.run(run_id="run-budget-consumed"):
        _emit_budget_consumed(
            state,
            model="gpt-4o-mini",
            input_tokens=9,
            output_tokens=3,
            cached_input_tokens=2,
        )

    matches = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name == "consumed"
    ]
    assert len(matches) == 1
    envelope = matches[0]
    wire = envelope.to_dict()
    assert envelope.cached_input_tokens == 2
    assert wire["cached_input_tokens"] == 2
    assert wire["usd_micros"] == 321


def test_no_event_emission_without_active_runtime_context(client, emitted_events):
    actguard.emit_event("budget", "check", {})

    matches = [
        env
        for env in emitted_events
        if env.category == "budget" and env.name == "check"
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
        actguard.emit_event("budget", "check", {})
    with client_b.run(run_id="run-b"):
        actguard.emit_event("budget", "check", {})

    a_budget = [e for e in events_a if e.category == "budget" and e.name == "check"]
    b_budget = [e for e in events_b if e.category == "budget" and e.name == "check"]
    assert len(a_budget) == 1
    assert len(b_budget) == 1
    assert a_budget[0].run_id == "run-a"
    assert b_budget[0].run_id == "run-b"

    client_a.close()
    client_b.close()
