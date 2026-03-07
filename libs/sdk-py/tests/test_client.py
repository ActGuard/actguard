from __future__ import annotations

import json

import actguard
from actguard.core.run_context import get_run_state
from actguard.core.state import get_current_state


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


def test_multiple_clients_do_not_leak_runtime_state():
    client_a = actguard.Client(api_key="key-a")
    client_b = actguard.Client(api_key="key-b")

    with client_a.run(run_id="outer"):
        outer = get_run_state()
        assert outer is not None
        assert outer.client is client_a

        with client_b.run(run_id="inner"):
            inner = get_run_state()
            assert inner is not None
            assert inner.client is client_b
            assert inner.run_id == "inner"

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

    reserve_id = client.reserve_budget(run_id="run-1", usd_limit_micros=500_000)

    assert reserve_id == "res-123"
    assert captured["url"] == "https://gw.example/api/v1/reserve"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["content_type"] == "application/json"
    assert captured["payload"] == {"run_id": "run-1", "usd_limit_micros": 500_000}
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
        run_id="run-1",
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
        "run_id": "run-1",
        "provider": "openai",
        "provider_model_id": "gpt-4o-mini",
        "input_tokens": 931,
        "cached_input_tokens": 0,
        "output_tokens": 30,
    }
    assert captured["timeout"] == client.timeout_s


def test_client_budget_guard_sets_and_clears_budget_state(monkeypatch):
    client = actguard.Client()
    assert get_run_state() is None
    assert get_current_state() is None

    monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-guard")
    monkeypatch.setattr(client, "settle_budget", lambda **_: None)

    with client.budget_guard(usd_limit=0.05) as guard:
        run_state = get_run_state()
        assert run_state is not None
        assert run_state.client is client
        assert run_state.budget_state is not None
        assert run_state.budget_state.user_id is None
        assert run_state.budget_state.reserve_id == "res-guard"
        assert get_current_state() is run_state.budget_state
        assert guard.run_id == run_state.run_id

    assert get_run_state() is None
    assert get_current_state() is None


def test_client_budget_guard_inside_run_reuses_existing_run(monkeypatch):
    client = actguard.Client()
    monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-shared")
    monkeypatch.setattr(client, "settle_budget", lambda **_: None)

    with client.run(run_id="run-existing", user_id="alice"):
        with client.budget_guard(usd_limit=0.1):
            run_state = get_run_state()
            assert run_state is not None
            assert run_state.run_id == "run-existing"
            assert run_state.user_id == "alice"
            assert run_state.budget_state is not None
            assert run_state.budget_state.user_id == "alice"


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

    with client.budget_guard(usd_limit=0.1) as guard:
        run_state = get_run_state()
        assert run_state is not None
        assert run_state.budget_reservation == {"reserve_id": "res_123"}
        run_state.budget_state.provider = "openai"
        run_state.budget_state.provider_model_id = "gpt-4o-mini"
        run_state.budget_state.input_tokens = 11
        run_state.budget_state.cached_input_tokens = 2
        run_state.budget_state.output_tokens = 7
        assert guard.run_id == run_state.run_id

    assert [name for name, _ in calls] == ["reserve", "settle"]
    assert calls[0][1]["usd_limit_micros"] == 100_000
    assert calls[1][1]["reserve_id"] == "res_123"
    assert calls[1][1]["provider"] == "openai"
    assert calls[1][1]["provider_model_id"] == "gpt-4o-mini"
    assert calls[1][1]["input_tokens"] == 11
    assert calls[1][1]["cached_input_tokens"] == 2
    assert calls[1][1]["output_tokens"] == 7


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

    with client_a.budget_guard(run_id="run-a", usd_limit=0.2):
        pass
    with client_b.budget_guard(run_id="run-b", usd_limit=0.3):
        pass

    assert calls_a[0][1]["run_id"] == "run-a"
    assert calls_a[0][1]["usd_limit_micros"] == 200_000
    assert calls_b[0][1]["run_id"] == "run-b"
    assert calls_b[0][1]["usd_limit_micros"] == 300_000
