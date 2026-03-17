"""Tests for LazyRequestBudgetSession."""
import asyncio
import threading
import warnings
from unittest.mock import patch

import pytest

from actguard import Client
from actguard.core.budget_context import (
    get_budget_state,
)
from actguard.core.budget_recorder import get_current_budget_recorder
from actguard.core.run_context import get_run_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**overrides) -> Client:
    defaults = dict(api_key="test-key", gateway_url="https://test.actguard.ai")
    defaults.update(overrides)
    return Client(**defaults)


# ---------------------------------------------------------------------------
# 1. Eager guard unchanged — BudgetGuard still reserves on enter
# ---------------------------------------------------------------------------


class TestEagerGuardUnchanged:
    def test_budget_guard_still_reserves_on_enter(self):
        client = _make_client()

        with patch.object(
            client, "reserve_budget", return_value="res-1"
        ) as mock_reserve:
            with patch.object(client, "settle_budget") as mock_settle:
                with client.run(user_id="u1"):
                    with client.budget_guard(usd_limit=1.0, user_id="u1"):
                        # reserve_budget should have been called during __enter__
                        mock_reserve.assert_called_once()

            # settle_budget should have been called during __exit__
            mock_settle.assert_called_once()


# ---------------------------------------------------------------------------
# 2. No-usage request — reserve and settle never called
# ---------------------------------------------------------------------------


class TestNoUsageRequest:
    def test_no_llm_calls_skips_reserve_and_settle(self):
        client = _make_client()

        with patch.object(client, "reserve_budget") as mock_reserve:
            with patch.object(client, "settle_budget") as mock_settle:
                with client.run(user_id="u1"):
                    with client.request_budget_session(
                        usd_limit=1.0, user_id="u1"
                    ) as session:
                        # No LLM calls here
                        assert get_budget_state() is not None
                        assert get_current_budget_recorder() is session

                mock_reserve.assert_not_called()
                mock_settle.assert_not_called()

    def test_contexts_cleaned_up_after_exit(self):
        client = _make_client()

        with patch.object(client, "reserve_budget"):
            with patch.object(client, "settle_budget"):
                with client.run(user_id="u1"):
                    with client.request_budget_session(
                        usd_limit=1.0, user_id="u1"
                    ):
                        pass

        assert get_run_state() is None
        assert get_budget_state() is None
        assert get_current_budget_recorder() is None


# ---------------------------------------------------------------------------
# 3. First record_usage triggers one reserve
# ---------------------------------------------------------------------------


class TestFirstRecordUsageTriggersReserve:
    def test_record_usage_triggers_reserve_once(self):
        client = _make_client()

        with patch.object(
            client, "reserve_budget", return_value="res-lazy"
        ) as mock_reserve:
            with patch.object(client, "settle_budget"):
                with client.run(user_id="u1"):
                    with client.request_budget_session(
                        usd_limit=1.0, user_id="u1"
                    ) as session:
                        session.record_usage(
                            provider="openai",
                            provider_model_id="gpt-4",
                            input_tokens=100,
                            output_tokens=50,
                        )
                        session.record_usage(
                            provider="openai",
                            provider_model_id="gpt-4",
                            input_tokens=200,
                            output_tokens=100,
                        )

                # reserve called exactly once despite two record_usage calls
                mock_reserve.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Reserve failure — warning emitted, settle NOT called
# ---------------------------------------------------------------------------


class TestReserveFailure:
    def test_reserve_failure_warns_and_skips_settle(self):
        client = _make_client()

        with patch.object(
            client,
            "reserve_budget",
            side_effect=ConnectionError("network down"),
        ) as mock_reserve:
            with patch.object(client, "settle_budget") as mock_settle:
                with client.run(user_id="u1"):
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter("always")
                        with client.request_budget_session(
                            usd_limit=1.0, user_id="u1"
                        ) as session:
                            session.record_usage(
                                provider="openai",
                                provider_model_id="gpt-4",
                                input_tokens=100,
                                output_tokens=50,
                            )

                mock_reserve.assert_called_once()
                mock_settle.assert_not_called()

        # A warning should have been emitted
        budget_warnings = [
            w for w in caught if "budget" in str(w.message).lower()
        ]
        assert len(budget_warnings) >= 1


# ---------------------------------------------------------------------------
# 6. Settle on exit — usage recorded, settle called
# ---------------------------------------------------------------------------


class TestSettleOnExit:
    def test_settle_called_with_accumulated_tokens(self):
        client = _make_client()

        with patch.object(
            client, "reserve_budget", return_value="res-lazy"
        ):
            with patch.object(client, "settle_budget") as mock_settle:
                with client.run(user_id="u1"):
                    with client.request_budget_session(
                        usd_limit=1.0, user_id="u1"
                    ) as session:
                        session.record_usage(
                            provider="openai",
                            provider_model_id="gpt-4",
                            input_tokens=100,
                            output_tokens=50,
                        )

                mock_settle.assert_called_once()
                call_kwargs = mock_settle.call_args[1]
                assert call_kwargs["reserve_id"] == "res-lazy"
                assert call_kwargs["input_tokens"] == 100
                assert call_kwargs["output_tokens"] == 50


# ---------------------------------------------------------------------------
# 7. Context isolation — two concurrent async sessions
# ---------------------------------------------------------------------------


class TestContextIsolation:
    @pytest.mark.asyncio
    async def test_concurrent_async_sessions_are_isolated(self):
        client = _make_client()

        results = {}

        async def session_a():
            with patch.object(
                client, "reserve_budget", return_value="res-a"
            ):
                with patch.object(client, "settle_budget"):
                    with client.run(user_id="user-a"):
                        async with client.request_budget_session(
                            usd_limit=1.0, user_id="user-a"
                        ) as session:
                            session.record_usage(
                                provider="openai",
                                provider_model_id="gpt-4",
                                input_tokens=100,
                                output_tokens=50,
                            )
                            results["a_tokens"] = session.local_tokens_used

        async def session_b():
            with patch.object(
                client, "reserve_budget", return_value="res-b"
            ):
                with patch.object(client, "settle_budget"):
                    with client.run(user_id="user-b"):
                        async with client.request_budget_session(
                            usd_limit=2.0, user_id="user-b"
                        ) as session:
                            session.record_usage(
                                provider="anthropic",
                                provider_model_id="claude-3",
                                input_tokens=200,
                                output_tokens=100,
                            )
                            results["b_tokens"] = session.local_tokens_used

        await asyncio.gather(session_a(), session_b())

        assert results["a_tokens"] == 150  # 100 + 50
        assert results["b_tokens"] == 300  # 200 + 100


# ---------------------------------------------------------------------------
# 8. Thread fallback — worker thread finds recorder via singleton registry
# ---------------------------------------------------------------------------


class TestThreadFallback:
    def test_worker_thread_finds_recorder_via_fallback(self):
        """A worker thread that doesn't inherit ContextVar should still
        find the recorder via the singleton fallback registry and trigger
        reserve + settle."""
        client = _make_client()
        errors = []

        with patch.object(
            client, "reserve_budget", return_value="res-thread"
        ) as mock_reserve:
            with patch.object(client, "settle_budget") as mock_settle:
                with client.run(user_id="u1"):
                    with client.request_budget_session(
                        usd_limit=1.0, user_id="u1"
                    ) as session:
                        # Verify recorder is set in main thread
                        assert get_current_budget_recorder() is session

                        def worker():
                            try:
                                # In a bare thread, ContextVar is NOT inherited
                                recorder = get_current_budget_recorder()
                                assert recorder is not None, (
                                    "fallback should return the singleton recorder"
                                )
                                recorder.record_usage(
                                    provider="openai",
                                    provider_model_id="gpt-4",
                                    input_tokens=100,
                                    output_tokens=50,
                                )
                            except Exception as exc:
                                errors.append(exc)

                        t = threading.Thread(target=worker)
                        t.start()
                        t.join()

                if errors:
                    raise errors[0]

                mock_reserve.assert_called_once()
                mock_settle.assert_called_once()

    def test_worker_reserve_race_with_exit(self):
        """When a worker thread is mid-record_usage (blocking on slow reserve),
        __exit__ must wait for the full record_usage() to complete before
        settling — so settle sees actual token counts, not 0."""
        import time

        client = _make_client()
        reserve_started = threading.Event()
        errors = []

        def slow_reserve(**kwargs):
            reserve_started.set()
            time.sleep(0.2)  # Simulate slow network call
            return "res-slow"

        with patch.object(
            client, "reserve_budget", side_effect=slow_reserve
        ) as mock_reserve:
            with patch.object(client, "settle_budget") as mock_settle:
                with client.run(user_id="u1"):
                    with client.request_budget_session(
                        usd_limit=1.0, user_id="u1"
                    ) as session:

                        def worker():
                            try:
                                # Full record_usage holds _record_lock through
                                # both _ensure_reserved() AND budget_context.record_usage()
                                session.record_usage(
                                    provider="openai",
                                    provider_model_id="gpt-4",
                                    input_tokens=100,
                                    output_tokens=50,
                                )
                            except Exception as exc:
                                errors.append(exc)

                        t = threading.Thread(target=worker)
                        t.start()
                        # Wait until worker is inside reserve_budget (holding _record_lock)
                        reserve_started.wait(timeout=5)
                        # Exit the session while worker still holds _record_lock.
                        # The _record_lock barrier in __exit__ should wait for the
                        # full record_usage() to complete before settling.

                    # Worker finishes after __exit__ waits on the lock barrier
                    t.join()

                if errors:
                    raise errors[0]

                mock_reserve.assert_called_once()
                # Settle must fire with actual token counts (not 0)
                mock_settle.assert_called_once()
                call_kwargs = mock_settle.call_args[1]
                assert call_kwargs["reserve_id"] == "res-slow"
                assert call_kwargs["input_tokens"] > 0, (
                    "settle must see actual input tokens, not 0"
                )
                assert call_kwargs["output_tokens"] > 0, (
                    "settle must see actual output tokens, not 0"
                )

    def test_multiple_concurrent_recorders_disable_fallback(self):
        """When multiple recorders are active, the singleton fallback must
        not return any of them (ambiguous) — worker thread gets None."""
        from actguard.core.budget_recorder import (
            _active_recorders,
            _active_recorders_lock,
            set_current_budget_recorder,
            reset_current_budget_recorder,
        )

        # Manually register two recorders to simulate concurrent sessions
        # (avoids nested run() which is disallowed).
        client_a = _make_client()
        client_b = _make_client()
        results = {}

        with patch.object(client_a, "reserve_budget", return_value="res-a"):
            with patch.object(client_a, "settle_budget"):
                with client_a.run(user_id="u1"):
                    with client_a.request_budget_session(
                        usd_limit=1.0, user_id="u1"
                    ) as session_a:
                        # Manually inject a second recorder into the registry
                        # to simulate a concurrent session from another thread.

                        class FakeRecorder:
                            def record_usage(self, **kw): pass
                            def check_limits(self): pass

                        fake = FakeRecorder()
                        with _active_recorders_lock:
                            _active_recorders[id(fake)] = fake

                        try:
                            def worker():
                                recorder = get_current_budget_recorder()
                                results["recorder"] = recorder

                            t = threading.Thread(target=worker)
                            t.start()
                            t.join()
                        finally:
                            with _active_recorders_lock:
                                _active_recorders.pop(id(fake), None)

        assert results["recorder"] is None, (
            "fallback should return None when multiple recorders are active"
        )
