"""Runtime integration tests for guards executed inside client.run(...)."""

import time

import pytest

import actguard
from actguard.core.run_context import get_run_state
from actguard.exceptions import (
    MaxAttemptsExceeded,
    MissingRuntimeContextError,
    MonitoringDegradedError,
    RateLimitExceeded,
    ToolTimeoutError,
)
from actguard.tools.idempotent import idempotent
from actguard.tools.max_attempts import max_attempts
from actguard.tools.rate_limit import rate_limit
from actguard.tools.timeout import timeout


def _raise_reporting_down(*_args, **_kwargs):
    raise RuntimeError("reporting down")


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
        with pytest.raises(RateLimitExceeded):
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


def test_tool_decorator_inherits_active_run_state():
    client = actguard.Client()

    @actguard.tool()
    def fn():
        state = get_run_state()
        assert state is not None
        return state.run_id, state.user_id

    with client.run(run_id="run-tool", user_id="alice"):
        assert fn() == ("run-tool", "alice")


def test_client_run_reporting_failure_warns_without_breaking_context(monkeypatch):
    client = actguard.Client()

    monkeypatch.setattr("actguard.reporting.emit_event", _raise_reporting_down)

    with pytest.warns(RuntimeWarning) as recorded:
        with client.run(run_id="run-reporting"):
            state = get_run_state()
            assert state is not None
            assert state.run_id == "run-reporting"

    assert len(recorded) == 2
    first = recorded[0].message.error
    second = recorded[1].message.error
    assert isinstance(first, MonitoringDegradedError)
    assert isinstance(second, MonitoringDegradedError)
    assert first.operation == "run.start"
    assert second.operation == "run.end"


def test_tool_decorator_reporting_failure_warns_without_breaking_function(monkeypatch):
    client = actguard.Client()
    monkeypatch.setenv("ACTGUARD_EMIT_ALL_TOOL_RUNS", "1")

    monkeypatch.setattr("actguard.reporting.emit_event", _raise_reporting_down)

    @actguard.tool()
    def fn():
        return "ok"

    with pytest.warns(RuntimeWarning) as recorded:
        with client.run(run_id="run-tool-warning"):
            assert fn() == "ok"

    operations = [warning.message.error.operation for warning in recorded]
    assert "tool.invoke" in operations
