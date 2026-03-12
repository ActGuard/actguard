from __future__ import annotations

import time

import pytest

import actguard
from actguard.exceptions import (
    CircuitOpenError,
    GuardError,
    MaxAttemptsExceeded,
    RateLimitExceeded,
    ToolTimeoutError,
)


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


def _find_events(events, *, category: str, name: str):
    return [e for e in events if e.category == category and e.name == name]


def test_tool_failure_emits_and_original_exception_propagates(client, emitted_events):
    @actguard.tool()
    def boom():
        raise ValueError("boom")

    with client.run(run_id="run-tool-failure", user_id="alice"):
        with pytest.raises(ValueError, match="boom"):
            boom()

    failures = _find_events(emitted_events, category="tool", name="failure")
    assert len(failures) == 1
    event = failures[0]
    assert event.run_id == "run-tool-failure"
    assert event.user_id == "alice"
    assert event.payload["tool_name"].endswith("boom")
    assert event.payload["error_type"] == "ValueError"
    assert event.payload["error_message"] == "boom"


def test_tool_failure_reporting_does_not_mask_primary_exception(monkeypatch):
    @actguard.tool()
    def boom():
        raise RuntimeError("primary")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("emit failed")

    monkeypatch.setattr("actguard.reporting.emit_event", _raise)

    with pytest.raises(RuntimeError, match="primary"):
        boom()


def test_tool_invoke_not_emitted_by_default(
    client, emitted_events, monkeypatch
):
    monkeypatch.delenv("ACTGUARD_EMIT_ALL_TOOL_RUNS", raising=False)

    @actguard.tool()
    def ok():
        return "ok"

    with client.run(run_id="run-default"):
        assert ok() == "ok"

    assert _find_events(emitted_events, category="tool", name="invoke") == []


def test_tool_invoke_emits_once_when_opted_in(
    client, emitted_events, monkeypatch
):
    monkeypatch.setenv("ACTGUARD_EMIT_ALL_TOOL_RUNS", "1")

    @actguard.tool()
    def ok():
        return "ok"

    with client.run(run_id="run-opt-in"):
        assert ok() == "ok"

    matches = _find_events(emitted_events, category="tool", name="invoke")
    assert len(matches) == 1
    assert matches[0].outcome == "success"


def test_max_attempts_emits_guard_blocked_with_optional_user_id(client, emitted_events):
    @actguard.max_attempts(calls=1)
    def once():
        return "ok"

    with client.run(run_id="run-max-attempts"):
        assert once() == "ok"
        with pytest.raises(MaxAttemptsExceeded):
            once()

    blocked = _find_events(emitted_events, category="guard", name="blocked")
    matches = [e for e in blocked if e.payload.get("guard_name") == "max_attempts"]
    assert len(matches) == 1
    event = matches[0]
    assert event.run_id == "run-max-attempts"
    assert event.user_id is None
    assert "user_id" not in event.to_dict()
    assert event.payload["limit"] == 1
    assert event.payload["used"] == 2


def test_idempotent_cached_return_emits_guard_intervention(client, emitted_events):
    calls = 0

    @actguard.idempotent
    def cached(*, idempotency_key: str):
        nonlocal calls
        calls += 1
        return calls

    with client.run(run_id="run-idem"):
        first = cached(idempotency_key="same")
        second = cached(idempotency_key="same")

    assert first == 1
    assert second == 1
    interventions = _find_events(emitted_events, category="guard", name="intervention")
    matches = [e for e in interventions if e.payload.get("guard_name") == "idempotent"]
    assert len(matches) == 1
    assert matches[0].payload["action"] == "return_cached"


def test_timeout_emits_guard_intervention(client, emitted_events):
    @actguard.timeout(0.01)
    def slow():
        time.sleep(0.1)

    with client.run(run_id="run-timeout"):
        with pytest.raises(ToolTimeoutError):
            slow()

    interventions = _find_events(emitted_events, category="guard", name="intervention")
    matches = [e for e in interventions if e.payload.get("guard_name") == "timeout"]
    assert len(matches) == 1
    assert matches[0].run_id == "run-timeout"


def test_rate_limit_emits_guard_blocked(client, emitted_events):
    @actguard.rate_limit(max_calls=1, period=60, scope="user_id")
    def fn(user_id: str):
        return user_id

    with client.run(run_id="run-rate", user_id="alice"):
        assert fn("alice") == "alice"
        with pytest.raises(RateLimitExceeded):
            fn("alice")

    blocked = _find_events(emitted_events, category="guard", name="blocked")
    matches = [e for e in blocked if e.payload.get("guard_name") == "rate_limit"]
    assert len(matches) == 1
    assert matches[0].payload["scope_value"] == "alice"


def test_circuit_breaker_emits_guard_blocked_when_open(client, emitted_events):
    @actguard.circuit_breaker(name="dep", max_fails=1, reset_timeout=60)
    def flaky():
        raise ConnectionError("down")

    with client.run(run_id="run-circuit"):
        with pytest.raises(ConnectionError):
            flaky()
        with pytest.raises(CircuitOpenError):
            flaky()

    blocked = _find_events(emitted_events, category="guard", name="blocked")
    matches = [e for e in blocked if e.payload.get("guard_name") == "circuit_breaker"]
    assert len(matches) == 1
    assert matches[0].run_id == "run-circuit"


def test_enforce_and_prove_emit_guard_blocked(client, emitted_events):
    @actguard.enforce([actguard.RequireFact("order_id", "order_id")])
    def delete_order(order_id: str):
        return order_id

    @actguard.prove(kind="item_id", extract="id", max_items=1, on_too_many="block")
    def list_items():
        return [{"id": "1"}, {"id": "2"}]

    with client.run(run_id="run-enforce-prove"):
        with pytest.raises(GuardError):
            delete_order(order_id="o-1")
        with actguard.session("sess-guard"):
            with pytest.raises(GuardError):
                list_items()

    blocked = _find_events(emitted_events, category="guard", name="blocked")
    names = {e.payload.get("guard_name") for e in blocked}
    assert "enforce" in names
    assert "prove" in names
