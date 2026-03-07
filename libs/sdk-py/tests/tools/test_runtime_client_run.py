"""Runtime integration tests for guards executed inside client.run(...)."""

import time

import pytest

import actguard
from actguard.exceptions import (
    MaxAttemptsExceeded,
    MissingRuntimeContextError,
    ToolTimeoutError,
)
from actguard.tools.idempotent import idempotent
from actguard.tools.max_attempts import max_attempts
from actguard.tools.rate_limit import rate_limit
from actguard.tools.timeout import timeout


def test_max_attempts_inside_client_run_without_budget_guard():
    client = actguard.Client()

    @max_attempts(calls=1)
    def fn() -> str:
        return "ok"

    with client.run(run_id="run-ma"):
        assert fn() == "ok"
        with pytest.raises(MaxAttemptsExceeded):
            fn()


def test_idempotent_inside_client_run_without_budget_guard():
    client = actguard.Client()
    calls = 0

    @idempotent
    def fn(*, idempotency_key: str) -> int:
        nonlocal calls
        calls += 1
        return calls

    with client.run(run_id="run-idem"):
        first = fn(idempotency_key="same")
        second = fn(idempotency_key="same")

    assert first == 1
    assert second == 1
    assert calls == 1


def test_timeout_reports_active_run_context_inside_client_run():
    client = actguard.Client(gateway_url="http://localhost:9999", api_key="test-key")
    assert client.event_client is not None
    events = []

    @timeout(0.01)
    def slow() -> None:
        time.sleep(0.1)

    client.event_client.enqueue = lambda envelope: events.append(envelope) or True

    with client.run(run_id="run-timeout"):
        with pytest.raises(ToolTimeoutError) as exc_info:
            slow()

    assert exc_info.value.run_id == "run-timeout"
    matches = [
        e
        for e in events
        if e.category == "guard"
        and e.name == "intervention"
        and e.payload.get("guard_name") == "timeout"
    ]
    assert len(matches) == 1
    assert matches[0].run_id == "run-timeout"
    client.close()


def test_rate_limit_reports_active_run_context_inside_client_run():
    client = actguard.Client(gateway_url="http://localhost:9999", api_key="test-key")
    assert client.event_client is not None
    events = []

    @rate_limit(max_calls=1, period=60)
    def fn() -> str:
        return "ok"

    client.event_client.enqueue = lambda envelope: events.append(envelope) or True

    with client.run(run_id="run-rate"):
        assert fn() == "ok"
        with pytest.raises(actguard.RateLimitExceeded):
            fn()

    matches = [
        e
        for e in events
        if e.category == "guard"
        and e.name == "blocked"
        and e.payload.get("guard_name") == "rate_limit"
    ]
    assert len(matches) == 1
    assert matches[0].run_id == "run-rate"
    client.close()


def test_missing_runtime_context_error_stays_clear():
    @max_attempts(calls=1)
    def fn() -> str:
        return "ok"

    with pytest.raises(MissingRuntimeContextError) as exc_info:
        fn()

    assert "client.run" in str(exc_info.value)
