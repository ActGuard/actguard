"""Tests for ActGuardMiddleware (Agno / ASGI integration)."""
import asyncio
import json
from unittest.mock import patch

import pytest

from actguard import Client
from actguard.core.budget_context import get_budget_state
from actguard.core.run_context import get_run_state
from actguard.exceptions import (
    ActGuardPaymentRequired,
    BudgetExceededError,
    NestedRuntimeContextError,
)
from actguard.integrations.agno import ActGuardMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**overrides) -> Client:
    defaults = dict(api_key="test-key", gateway_url="https://test.actguard.ai")
    defaults.update(overrides)
    return Client(**defaults)


def _http_scope(headers=None):
    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode(), v.encode()))
    return {"type": "http", "headers": raw_headers}


def _ws_scope(headers=None):
    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode(), v.encode()))
    return {"type": "websocket", "headers": raw_headers}


def _lifespan_scope():
    return {"type": "lifespan"}


async def _noop_receive():
    return {}


async def _noop_send(message):
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestActGuardMiddleware:
    """Core middleware behaviour."""

    @pytest.mark.asyncio
    async def test_sets_run_and_budget_context_during_request(self):
        """Budget and run context should be active when the inner app runs."""
        client = _make_client()
        captured = {}

        async def inner_app(scope, receive, send):
            captured["run_state"] = get_run_state()
            captured["budget_state"] = get_budget_state()

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=1.0,
            default_user_id="test-user",
        )

        await mw(_http_scope(), _noop_receive, _noop_send)

        assert captured["run_state"] is not None
        assert captured["run_state"].user_id == "test-user"
        assert captured["budget_state"] is not None

    @pytest.mark.asyncio
    async def test_contexts_not_active_after_request(self):
        """After the middleware finishes, contexts should be cleaned up."""
        client = _make_client()

        async def inner_app(scope, receive, send):
            pass

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="u1",
        )

        await mw(_http_scope(), _noop_receive, _noop_send)

        assert get_run_state() is None
        assert get_budget_state() is None

    @pytest.mark.asyncio
    async def test_passthrough_for_lifespan_scope(self):
        """Non-http/ws scopes should pass through without wrapping."""
        client = _make_client()
        called = False

        async def inner_app(scope, receive, send):
            nonlocal called
            called = True

        mw = ActGuardMiddleware(inner_app, client=client)
        await mw(_lifespan_scope(), _noop_receive, _noop_send)
        assert called

    @pytest.mark.asyncio
    async def test_websocket_scope_is_wrapped(self):
        """WebSocket scopes should also get run+budget context."""
        client = _make_client()
        captured = {}

        async def inner_app(scope, receive, send):
            captured["run_state"] = get_run_state()
            captured["budget_state"] = get_budget_state()

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="ws-user",
        )

        await mw(_ws_scope(), _noop_receive, _noop_send)

        assert captured["run_state"] is not None
        assert captured["budget_state"] is not None


class TestUserIdExtraction:
    """User ID resolution from headers and custom resolver."""

    @pytest.mark.asyncio
    async def test_extracts_user_id_from_header(self):
        """Should read user ID from the configured header."""
        client = _make_client()
        captured = {}

        async def inner_app(scope, receive, send):
            captured["run_state"] = get_run_state()

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            user_id_header="X-User-Id",
            default_user_id="fallback",
        )

        scope = _http_scope(headers={"X-User-Id": "alice"})
        await mw(scope, _noop_receive, _noop_send)

        assert captured["run_state"].user_id == "alice"

    @pytest.mark.asyncio
    async def test_falls_back_to_default_user_id(self):
        """No header present → use default_user_id."""
        client = _make_client()
        captured = {}

        async def inner_app(scope, receive, send):
            captured["run_state"] = get_run_state()

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="fallback-user",
        )

        await mw(_http_scope(), _noop_receive, _noop_send)

        assert captured["run_state"].user_id == "fallback-user"

    @pytest.mark.asyncio
    async def test_custom_user_id_resolver(self):
        """user_id_resolver callback should override header extraction."""
        client = _make_client()
        captured = {}

        async def inner_app(scope, receive, send):
            captured["run_state"] = get_run_state()

        def resolver(scope):
            return "resolved-bob"

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            user_id_resolver=resolver,
            default_user_id="should-not-use",
        )

        await mw(_http_scope(), _noop_receive, _noop_send)

        assert captured["run_state"].user_id == "resolved-bob"


class TestSequentialRequests:
    """Sequential requests should get fresh, isolated contexts."""

    @pytest.mark.asyncio
    async def test_sequential_requests_get_separate_run_ids(self):
        """Each request should receive its own run ID."""
        client = _make_client()
        run_ids = []

        async def inner_app(scope, receive, send):
            run_ids.append(get_run_state().run_id)

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=1.0,
            default_user_id="u",
        )

        await mw(_http_scope(), _noop_receive, _noop_send)
        await mw(_http_scope(), _noop_receive, _noop_send)

        assert len(run_ids) == 2
        assert run_ids[0] != run_ids[1]

    @pytest.mark.asyncio
    async def test_context_clean_between_requests(self):
        """Context should be fully cleaned up between sequential requests."""
        client = _make_client()

        async def inner_app(scope, receive, send):
            assert get_run_state() is not None
            assert get_budget_state() is not None

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="u",
        )

        await mw(_http_scope(), _noop_receive, _noop_send)

        # After the request, contexts should be gone
        assert get_run_state() is None
        assert get_budget_state() is None

        await mw(_http_scope(), _noop_receive, _noop_send)


class TestBudgetExceededHandling:
    """Budget exceeded errors should be caught and returned as 402 responses."""

    @pytest.mark.asyncio
    async def test_budget_exceeded_returns_402_by_default(self):
        """BudgetExceededError from inner app should produce a 402 JSON response."""
        client = _make_client()

        async def inner_app(scope, receive, send):
            raise BudgetExceededError(
                user_id="u1",
                tokens_used=1000,
                usd_used=0.55,
                usd_limit=0.50,
                limit_type="usd",
            )

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="u1",
        )

        sent_messages = []

        async def capture_send(message):
            sent_messages.append(message)

        await mw(_http_scope(), _noop_receive, capture_send)

        assert len(sent_messages) == 2
        assert sent_messages[0]["type"] == "http.response.start"
        assert sent_messages[0]["status"] == 402
        body = json.loads(sent_messages[1]["body"])
        assert body["error"]["code"] == "budget.limit_exceeded"
        assert body["error"]["reason"] == "budget_exhausted"

    @pytest.mark.asyncio
    async def test_payment_required_returns_402_by_default(self):
        """ActGuardPaymentRequired from inner app should produce a 402 JSON response."""
        client = _make_client()

        async def inner_app(scope, receive, send):
            raise ActGuardPaymentRequired(path="/v1/budget/reserve", status=402)

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="u1",
        )

        sent_messages = []

        async def capture_send(message):
            sent_messages.append(message)

        await mw(_http_scope(), _noop_receive, capture_send)

        assert len(sent_messages) == 2
        assert sent_messages[0]["status"] == 402
        body = json.loads(sent_messages[1]["body"])
        assert body["error"]["code"] == "budget.payment_required"
        assert body["error"]["reason"] == "payment_required"

    @pytest.mark.asyncio
    async def test_on_budget_exceeded_callback_called(self):
        """Custom callback should be invoked instead of default 402."""
        client = _make_client()
        callback_calls = []

        async def inner_app(scope, receive, send):
            raise BudgetExceededError(
                user_id="u1",
                tokens_used=1000,
                usd_used=0.55,
                usd_limit=0.50,
                limit_type="usd",
            )

        async def custom_handler(scope, receive, send, exc):
            callback_calls.append(exc)

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="u1",
            on_budget_exceeded=custom_handler,
        )

        sent_messages = []

        async def capture_send(message):
            sent_messages.append(message)

        await mw(_http_scope(), _noop_receive, capture_send)

        assert len(callback_calls) == 1
        assert isinstance(callback_calls[0], BudgetExceededError)
        # Default 402 should NOT have been sent
        assert len(sent_messages) == 0


class TestConcurrentRequests:
    """Concurrent async requests must not interfere with each other."""

    @pytest.mark.asyncio
    async def test_concurrent_requests_no_nested_context_error(self):
        """Two concurrent ASGI requests should each get their own context
        without raising NestedRuntimeContextError."""
        client = _make_client()
        results: dict[str, dict] = {}
        barrier = asyncio.Barrier(2)

        async def inner_app(scope, receive, send):
            user_id = get_run_state().user_id
            # Synchronize so both requests are active at the same time
            await barrier.wait()
            results[user_id] = {
                "run_state": get_run_state(),
                "budget_state": get_budget_state(),
            }

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=1.0,
            user_id_header="X-User-Id",
            default_user_id="fallback",
        )

        scope_a = _http_scope(headers={"X-User-Id": "user-a"})
        scope_b = _http_scope(headers={"X-User-Id": "user-b"})

        # Run two requests concurrently as separate tasks
        task_a = asyncio.create_task(mw(scope_a, _noop_receive, _noop_send))
        task_b = asyncio.create_task(mw(scope_b, _noop_receive, _noop_send))

        await asyncio.gather(task_a, task_b)

        # Both requests should have completed with their own context
        assert "user-a" in results
        assert "user-b" in results
        assert results["user-a"]["run_state"].user_id == "user-a"
        assert results["user-b"]["run_state"].user_id == "user-b"
        assert results["user-a"]["run_state"] is not results["user-b"]["run_state"]
        assert results["user-a"]["budget_state"] is not None
        assert results["user-b"]["budget_state"] is not None

        # After both complete, global state should be clean
        assert get_run_state() is None
        assert get_budget_state() is None


class TestSilentDegradation:
    """When actguard infrastructure fails, the middleware should degrade silently."""

    @pytest.mark.asyncio
    async def test_nested_runtime_context_error_degrades_silently(self):
        """NestedRuntimeContextError from client.run() should not crash the app."""
        client = _make_client()
        called = False

        async def inner_app(scope, receive, send):
            nonlocal called
            called = True

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="u1",
        )

        # Simulate NestedRuntimeContextError by making client.run() raise
        with patch.object(
            client,
            "run",
            side_effect=NestedRuntimeContextError("stale context detected"),
        ):
            await mw(_http_scope(), _noop_receive, _noop_send)

        assert called, "Inner app should still run without actguard protection"

    @pytest.mark.asyncio
    async def test_generic_exception_in_context_setup_degrades_silently(self):
        """A generic RuntimeError during context setup should not crash the app."""
        client = _make_client()
        called = False

        async def inner_app(scope, receive, send):
            nonlocal called
            called = True

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="u1",
        )

        with patch.object(
            client,
            "run",
            side_effect=RuntimeError("connection refused"),
        ):
            await mw(_http_scope(), _noop_receive, _noop_send)

        assert called, "Inner app should still run without actguard protection"

    @pytest.mark.asyncio
    async def test_budget_exceeded_from_context_setup_is_not_silenced(self):
        """BudgetExceededError should propagate even during context setup."""
        client = _make_client()

        async def inner_app(scope, receive, send):
            pass

        mw = ActGuardMiddleware(
            inner_app,
            client=client,
            usd_limit=0.5,
            default_user_id="u1",
        )

        with patch.object(
            client,
            "run",
            side_effect=BudgetExceededError(
                user_id="u1",
                tokens_used=1000,
                usd_used=0.55,
                usd_limit=0.50,
                limit_type="usd",
            ),
        ):
            with pytest.raises(BudgetExceededError):
                await mw(_http_scope(), _noop_receive, _noop_send)
