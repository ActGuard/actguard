"""Tests for the event emission pipeline via emit_violation()."""
from __future__ import annotations

import pytest

import actguard
from actguard.exceptions import BudgetExceededError
from actguard.budget import BudgetGuard


@pytest.fixture()
def event_client():
    """Configure a fake gateway so events_enabled=True; yield the live client."""
    actguard.configure(gateway_url="http://localhost:9999", api_key="test-key")
    from actguard.events.client import get_client

    client = get_client()
    yield client
    # Teardown: disable events so other tests are unaffected
    actguard.configure()  # no gateway_url → _config=None → client=None


def test_budget_exceeded_emits_violation_event(event_client):
    error = BudgetExceededError(
        user_id="alice",
        tokens_used=1000,
        usd_used=0.05,
        token_limit=500,
        usd_limit=None,
        limit_type="token",
    )

    with BudgetGuard(user_id="alice", token_limit=500):
        actguard.emit_violation(error)

    assert event_client._queue.qsize() == 1
    envelope = event_client._queue.get_nowait()
    assert envelope.category == "budget"
    assert envelope.name == "limit_exceeded"
    assert envelope.severity == "error"
    assert envelope.outcome == "blocked"
    assert envelope.payload["user_id"] == "alice"
    assert envelope.payload["tokens_used"] == 1000
    assert envelope.payload["token_limit"] == 500
    assert envelope.payload["limit_type"] == "token"


def test_emit_violation_no_op_without_config():
    # No event_client fixture → no configure() called
    error = BudgetExceededError(
        user_id="alice",
        tokens_used=100,
        usd_used=0.01,
        token_limit=50,
        usd_limit=None,
        limit_type="token",
    )
    actguard.emit_violation(error)  # must not raise
    from actguard.events.client import get_client

    assert get_client() is None
