from __future__ import annotations

import io
import json
import urllib.error

import pytest

import actguard
from actguard.core.budget_context import get_budget_state
from actguard.core.run_context import get_run_state
from actguard.core.state import get_current_state
from actguard.exceptions import (
    ActGuardPaymentRequired,
    BudgetTransportError,
    MissingRuntimeContextError,
    NestedRuntimeContextError,
)


def test_client_from_file_creates_usable_client(tmp_path):
    config_file = tmp_path / "actguard.json"
    config_file.write_text(
        json.dumps(
            {
                "gateway_url": "https://api.actguard.io",
                "api_key": "sk-test",
            }
        )
    )

    client = actguard.Client.from_file(config_file)

    assert client.gateway_url == "https://api.actguard.io"
    assert client.api_key == "sk-test"

    with client.run(user_id="alice", run_id="run-1"):
        state = get_run_state()
        assert state is not None
        assert state.client is client
        assert state.user_id == "alice"
        assert state.run_id == "run-1"


def test_client_run_without_user_id_is_supported():
    client = actguard.Client()

    with client.run(run_id="run-no-user"):
        state = get_run_state()
        assert state is not None
        assert state.client is client
        assert state.user_id is None
        assert state.run_id == "run-no-user"


def test_nested_client_runs_raise_and_keep_outer_context():
    client_a = actguard.Client(api_key="key-a")
    client_b = actguard.Client(api_key="key-b")

    with client_a.run(run_id="outer"):
        outer = get_run_state()
        assert outer is not None
        assert outer.client is client_a

        with pytest.raises(NestedRuntimeContextError):
            with client_b.run(run_id="inner"):
                pass

        restored = get_run_state()
        assert restored is not None
        assert restored.client is client_a
        assert restored.run_id == "outer"

    assert get_run_state() is None


def test_sequential_runs_restore_context_correctly():
    client = actguard.Client()

    assert get_run_state() is None

    with client.run(run_id="first"):
        assert get_run_state() is not None
        assert get_run_state().run_id == "first"

    assert get_run_state() is None

    with client.run(run_id="second"):
        assert get_run_state() is not None
        assert get_run_state().run_id == "second"

    assert get_run_state() is None


def test_run_context_clears_after_exception():
    client = actguard.Client()

    with pytest.raises(ValueError, match="boom"):
        with client.run(run_id="run-error"):
            assert get_run_state() is not None
            raise ValueError("boom")

    assert get_run_state() is None


def test_reserve_budget_posts_expected_request_shape(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"reserve_id":"res-123"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("Content-type")
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    reserve_id = client.reserve_budget(
        run_id="run-1",
        usd_limit_micros=500_000,
        plan_key="pro",
        user_id="alice",
    )

    assert reserve_id == "res-123"
    assert captured["url"] == "https://gw.example/api/v1/reserve"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["content_type"] == "application/json"
    assert captured["payload"] == {
        "run_id": "run-1",
        "usd_limit_micros": 500_000,
        "plan_key": "pro",
        "user_id": "alice",
    }
    assert captured["timeout"] == client.timeout_s


def test_settle_budget_posts_expected_request_shape(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client.settle_budget(
        reserve_id="res-123",
        provider="openai",
        provider_model_id="gpt-4o-mini",
        input_tokens=931,
        cached_input_tokens=0,
        output_tokens=30,
    )

    assert captured["url"] == "https://gw.example/api/v1/settle"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["payload"] == {
        "reserve_id": "res-123",
        "provider": "openai",
        "provider_model_id": "gpt-4o-mini",
        "input_tokens": 931,
        "cached_input_tokens": 0,
        "output_tokens": 30,
    }
    assert captured["timeout"] == client.timeout_s


def test_reserve_budget_omits_limit_when_unknown(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"reserve_id":"res-123"}'

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    reserve_id = client.reserve_budget(run_id="run-1", usd_limit_micros=None)

    assert reserve_id == "res-123"
    assert captured["payload"] == {"run_id": "run-1"}
    assert captured["timeout"] == client.timeout_s


def test_reserve_budget_omits_optional_root_metadata_when_unknown(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"reserve_id":"res-123"}'

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    reserve_id = client.reserve_budget(
        run_id="run-1",
        usd_limit_micros=500_000,
        plan_key="",
        user_id="",
    )

    assert reserve_id == "res-123"
    assert captured["payload"] == {"run_id": "run-1", "usd_limit_micros": 500_000}
    assert captured["timeout"] == client.timeout_s


def test_reserve_budget_402_raises_payment_required_without_retry(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        max_retries=8,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        raise urllib.error.HTTPError(
            request.full_url,
            402,
            "Payment Required",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"payment_required"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))

    with pytest.raises(ActGuardPaymentRequired) as excinfo:
        client.reserve_budget(run_id="run-1", usd_limit_micros=500_000)

    assert attempts["count"] == 1
    assert sleeps == []
    assert excinfo.value.path == "/api/v1/reserve"
    assert excinfo.value.status == 402
    assert isinstance(excinfo.value.__cause__, urllib.error.HTTPError)
    assert excinfo.value.__cause__.code == 402


def test_settle_budget_402_raises_payment_required_without_retry(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        max_retries=8,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        raise urllib.error.HTTPError(
            request.full_url,
            402,
            "Payment Required",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"payment_required"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))

    with pytest.raises(ActGuardPaymentRequired) as excinfo:
        client.settle_budget(
            reserve_id="res-123",
            provider="openai",
            provider_model_id="gpt-4o-mini",
            input_tokens=11,
            cached_input_tokens=0,
            output_tokens=7,
        )

    assert attempts["count"] == 1
    assert sleeps == []
    assert excinfo.value.path == "/api/v1/settle"
    assert excinfo.value.status == 402
    assert isinstance(excinfo.value.__cause__, urllib.error.HTTPError)
    assert excinfo.value.__cause__.code == 402


def test_reserve_budget_401_remains_budget_transport_error(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        max_retries=8,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b""),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))

    with pytest.raises(BudgetTransportError) as excinfo:
        client.reserve_budget(run_id="run-1", usd_limit_micros=500_000)

    assert attempts["count"] == 1
    assert sleeps == []
    assert "status 401" in str(excinfo.value)


def test_reserve_budget_500_retries_before_budget_transport_error(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        max_retries=2,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(b""),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr("random.uniform", lambda lower, upper: 0.0)

    with pytest.raises(BudgetTransportError) as excinfo:
        client.reserve_budget(run_id="run-1", usd_limit_micros=500_000)

    assert attempts["count"] == 3
    assert sleeps == [0.2, 0.4]
    assert "HTTPError" in str(excinfo.value)


def test_client_budget_guard_sets_and_clears_budget_state(monkeypatch):
    client = actguard.Client()
    assert get_run_state() is None
    assert get_budget_state() is None
    assert get_current_state() is None

    monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-guard")
    monkeypatch.setattr(client, "settle_budget", lambda **_: None)

    with client.run(run_id="run-budget-state"):
        with client.budget_guard(usd_limit=0.05, plan_key="pro") as guard:
            run_state = get_run_state()
            budget_state = get_budget_state()
            assert run_state is not None
            assert run_state.client is client
            assert not hasattr(run_state, "budget_state")
            assert budget_state is not None
            assert budget_state.user_id is None
            assert budget_state.reserve_id == "res-guard"
            assert budget_state.plan_key == "pro"
            assert get_current_state() is budget_state
            assert guard.run_id == run_state.run_id

    assert get_run_state() is None
    assert get_budget_state() is None
    assert get_current_state() is None


def test_client_budget_guard_requires_active_run():
    client = actguard.Client()

    with pytest.raises(MissingRuntimeContextError):
        with client.budget_guard(usd_limit=0.05):
            pass


def test_client_budget_guard_inside_run_reuses_existing_run(monkeypatch):
    client = actguard.Client()
    monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-shared")
    monkeypatch.setattr(client, "settle_budget", lambda **_: None)

    with client.run(run_id="run-existing", user_id="alice"):
        assert get_budget_state() is None
        with client.budget_guard(usd_limit=0.1):
            run_state = get_run_state()
            budget_state = get_budget_state()
            assert run_state is not None
            assert run_state.run_id == "run-existing"
            assert run_state.user_id == "alice"
            assert not hasattr(run_state, "budget_state")
            assert budget_state is not None
            assert budget_state.user_id == "alice"
        assert get_budget_state() is None
        assert get_run_state() is run_state


def test_client_budget_guard_calls_reserve_and_settle_hooks(monkeypatch):
    client = actguard.Client()
    calls = []

    def reserve(**kwargs):
        calls.append(("reserve", kwargs))
        return "res_123"

    def settle(**kwargs):
        calls.append(("settle", kwargs))
        return None

    monkeypatch.setattr(client, "reserve_budget", reserve)
    monkeypatch.setattr(client, "settle_budget", settle)

    with client.run(run_id="run-hooks"):
        with client.budget_guard(usd_limit=0.1, plan_key="pro") as guard:
            budget_state = get_budget_state()
            assert budget_state is not None
            assert budget_state.reserve_id == "res_123"
            assert budget_state.plan_key == "pro"
            budget_state.provider = "openai"
            budget_state.provider_model_id = "gpt-4o-mini"
            budget_state.input_tokens = 11
            budget_state.cached_input_tokens = 2
            budget_state.output_tokens = 7
            assert guard.run_id == budget_state.run_id

    assert [name for name, _ in calls] == ["reserve", "settle"]
    assert calls[0][1]["usd_limit_micros"] == 100_000
    assert calls[0][1]["plan_key"] == "pro"
    assert calls[0][1]["user_id"] is None
    assert calls[1][1]["reserve_id"] == "res_123"
    assert calls[1][1]["provider"] == "openai"
    assert calls[1][1]["provider_model_id"] == "gpt-4o-mini"
    assert calls[1][1]["input_tokens"] == 11
    assert calls[1][1]["cached_input_tokens"] == 2
    assert calls[1][1]["output_tokens"] == 7
    assert "run_id" not in calls[1][1]


def test_budget_guard_no_leakage_between_clients(monkeypatch):
    client_a = actguard.Client()
    client_b = actguard.Client()
    calls_a = []
    calls_b = []

    monkeypatch.setattr(
        client_a,
        "reserve_budget",
        lambda **kwargs: calls_a.append(("reserve", kwargs)) or "a",
    )
    monkeypatch.setattr(
        client_a,
        "settle_budget",
        lambda **kwargs: calls_a.append(("settle", kwargs)),
    )
    monkeypatch.setattr(
        client_b,
        "reserve_budget",
        lambda **kwargs: calls_b.append(("reserve", kwargs)) or "b",
    )
    monkeypatch.setattr(
        client_b,
        "settle_budget",
        lambda **kwargs: calls_b.append(("settle", kwargs)),
    )

    with client_a.run(run_id="run-a"):
        with client_a.budget_guard(run_id="run-a", usd_limit=0.2):
            pass
    with client_b.run(run_id="run-b"):
        with client_b.budget_guard(run_id="run-b", usd_limit=0.3):
            pass

    assert calls_a[0][1]["run_id"] == "run-a"
    assert calls_a[0][1]["usd_limit_micros"] == 200_000
    assert calls_b[0][1]["run_id"] == "run-b"
    assert calls_b[0][1]["usd_limit_micros"] == 300_000


def test_client_close_waits_for_event_client_shutdown():
    client = actguard.Client(gateway_url="https://gw.example", api_key="sk-test")
    assert client.event_client is not None
    seen = {}

    class _Recorder:
        def close(self, *, wait: bool) -> None:
            seen["wait"] = wait

    client._event_client = _Recorder()

    client.close()

    assert seen == {"wait": True}
    assert client.event_client is None
