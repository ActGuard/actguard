from __future__ import annotations

import io
import json
import ssl
import urllib.error

import pytest

import actguard
from actguard.client import DEFAULT_GATEWAY_URL
from actguard.core.budget_context import get_budget_state, record_usage
from actguard.core.run_context import get_run_state
from actguard.core.state import get_current_state
from actguard.costs import CuTariff
from actguard.exceptions import (
    ActGuardPaymentRequired,
    BudgetExceededError,
    BudgetTransportError,
    MissingRuntimeContextError,
    MonitoringDegradedError,
    NestedRuntimeContextError,
)
from actguard.reporting import record_response_usage


@pytest.fixture(autouse=True)
def stub_cu_tariff(monkeypatch):
    tariff = CuTariff.from_payload(
        {
            "tariff_version": "v1-test",
            "cu_per_usd": 1000,
            "registry_version": "registry-test",
            "llm": {
                "default": {
                    "input_cu_per_1k": 1,
                    "output_cu_per_1k": 4,
                    "cached_cu_per_1k": 1,
                }
            },
            "tools": {},
        }
    )
    monkeypatch.setattr(
        actguard.Client,
        "get_cu_tariff",
        lambda self, force_refresh=False: tariff,
    )
    return tariff


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


def test_client_defaults_split_budget_and_event_transport_config():
    client = actguard.Client()

    assert client.gateway_url == DEFAULT_GATEWAY_URL
    assert client.reporting_config.gateway_url == DEFAULT_GATEWAY_URL
    assert client.budget_timeout_s == 3.0
    assert client.budget_max_retries == 1
    assert client.event_timeout_s == 5.0
    assert client.event_max_retries == 8
    assert client.timeout_s == client.event_timeout_s
    assert client.max_retries == client.event_max_retries


def test_client_explicit_gateway_url_overrides_default():
    client = actguard.Client(gateway_url="https://gw.example")

    assert client.gateway_url == "https://gw.example"
    assert client.reporting_config.gateway_url == "https://gw.example"


def test_legacy_timeout_alias_populates_both_transport_configs():
    client = actguard.Client(timeout_s=2.5, max_retries=3)

    assert client.timeout_s == 2.5
    assert client.max_retries == 3
    assert client.budget_timeout_s == 2.5
    assert client.budget_max_retries == 3
    assert client.event_timeout_s == 2.5
    assert client.event_max_retries == 3


def test_subsystem_specific_transport_config_overrides_legacy_aliases():
    client = actguard.Client(
        timeout_s=9.0,
        max_retries=4,
        budget_timeout_s=0.75,
        budget_max_retries=1,
    )

    assert client.timeout_s == 9.0
    assert client.max_retries == 4
    assert client.budget_timeout_s == 0.75
    assert client.budget_max_retries == 1
    assert client.event_timeout_s == 9.0
    assert client.event_max_retries == 4


def test_client_from_file_accepts_split_transport_config(tmp_path):
    config_file = tmp_path / "actguard.json"
    config_file.write_text(
        json.dumps(
            {
                "gateway_url": "https://api.actguard.io",
                "api_key": "sk-test",
                "budget_timeout_s": 0.5,
                "budget_max_retries": 0,
                "event_timeout_s": 7.0,
                "event_max_retries": 2,
            }
        )
    )

    client = actguard.Client.from_file(config_file)

    assert client.budget_timeout_s == 0.5
    assert client.budget_max_retries == 0
    assert client.event_timeout_s == 7.0
    assert client.event_max_retries == 2


def test_reserve_budget_posts_expected_request_shape(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"reserve_id":"res-123"}'

    def fake_urlopen(request, timeout, context=None):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("Content-type")
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        captured["context"] = context
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    reserve_response = client.reserve_budget(
        run_id="run-1",
        cost_limit=500,
        plan_key="pro",
        user_id="alice",
    )

    assert reserve_response == {"status": "reserved", "reserve_id": "res-123"}
    assert captured["url"] == "https://gw.example/api/v1/reserve"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["content_type"] == "application/json"
    assert captured["payload"] == {
        "run_id": "run-1",
        "cost_limit": 500,
        "plan_key": "pro",
        "user_id": "alice",
    }
    assert captured["timeout"] == pytest.approx(client.budget_timeout_s, rel=1e-3)
    assert isinstance(captured["context"], ssl.SSLContext)


def test_settle_budget_posts_expected_request_shape(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"status":"settled","settled_micros":123}'

    def fake_urlopen(request, timeout, context=None):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        captured["context"] = context
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = client.settle_budget(
        reserve_id="res-123",
        input_tokens=931,
        cached_input_tokens=0,
        output_tokens=30,
        usage_breakdown=[
            {
                "provider": "openai",
                "provider_model_id": "gpt-4o-mini",
                "input_tokens": 931,
                "cached_input_tokens": 0,
                "output_tokens": 30,
            }
        ],
    )

    assert captured["url"] == "https://gw.example/api/v1/settle"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["payload"] == {
        "reserve_id": "res-123",
        "input_tokens": 931,
        "cached_input_tokens": 0,
        "output_tokens": 30,
        "usage_breakdown": [
            {
                "provider": "openai",
                "provider_model_id": "gpt-4o-mini",
                "input_tokens": 931,
                "cached_input_tokens": 0,
                "output_tokens": 30,
            }
        ],
    }
    assert response == {"status": "settled", "settled_micros": 123}
    assert captured["timeout"] == pytest.approx(client.budget_timeout_s, rel=1e-3)
    assert isinstance(captured["context"], ssl.SSLContext)


def test_settle_budget_includes_optional_backend_schema_fields(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return (
                b'{"status":"settled","settled_micros":123,'
                b'"overshoot_micros":45}'
            )

    def fake_urlopen(request, timeout, context=None):
        captured["payload"] = json.loads(request.data.decode())
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    response = client.settle_budget(
        reserve_id="res-123",
        input_tokens=931,
        cached_input_tokens=0,
        output_tokens=30,
        usage_breakdown=[
            {
                "provider": "openai",
                "provider_model_id": "gpt-4o-mini",
                "input_tokens": 931,
                "cached_input_tokens": 0,
                "output_tokens": 30,
                "scope_name": "search_tool",
            }
        ],
        cache_write_tokens_5m=12,
        cache_write_tokens_1h=34,
        web_search_count=2,
        reasoning_effort="high",
    )

    assert captured["payload"] == {
        "reserve_id": "res-123",
        "input_tokens": 931,
        "cached_input_tokens": 0,
        "output_tokens": 30,
        "usage_breakdown": [
            {
                "provider": "openai",
                "provider_model_id": "gpt-4o-mini",
                "input_tokens": 931,
                "cached_input_tokens": 0,
                "output_tokens": 30,
                "scope_name": "search_tool",
            }
        ],
        "cache_write_tokens_5m": 12,
        "cache_write_tokens_1h": 34,
        "web_search_count": 2,
        "reasoning_effort": "high",
    }
    assert response == {
        "status": "settled",
        "settled_micros": 123,
        "overshoot_micros": 45,
    }


def test_reserve_budget_http_gateway_omits_ssl_context(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="http://localhost:8787",
        api_key="sk-test",
        budget_max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"reserve_id":"res-123"}'

    def fake_urlopen(request, timeout, context=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["context"] = context
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    reserve_response = client.reserve_budget(run_id="run-1", cost_limit=500)

    assert reserve_response == {"status": "reserved", "reserve_id": "res-123"}
    assert captured["url"] == "http://localhost:8787/api/v1/reserve"
    assert captured["timeout"] == pytest.approx(client.budget_timeout_s, rel=1e-3)
    assert captured["context"] is None


def test_reserve_budget_omits_limit_when_unknown(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"reserve_id":"res-123"}'

    def fake_urlopen(request, timeout, context=None):
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        captured["context"] = context
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    reserve_response = client.reserve_budget(run_id="run-1", cost_limit=None)

    assert reserve_response == {"status": "reserved", "reserve_id": "res-123"}
    assert captured["payload"] == {"run_id": "run-1"}
    assert captured["timeout"] == pytest.approx(client.budget_timeout_s, rel=1e-3)
    assert isinstance(captured["context"], ssl.SSLContext)


def test_reserve_budget_omits_optional_root_metadata_when_unknown(monkeypatch):
    captured = {}
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=0,
    )

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b'{"reserve_id":"res-123"}'

    def fake_urlopen(request, timeout, context=None):
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        captured["context"] = context
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    reserve_response = client.reserve_budget(
        run_id="run-1",
        cost_limit=500,
        plan_key="",
        user_id="",
    )

    assert reserve_response == {"status": "reserved", "reserve_id": "res-123"}
    assert captured["payload"] == {"run_id": "run-1", "cost_limit": 500}
    assert captured["timeout"] == pytest.approx(client.budget_timeout_s, rel=1e-3)
    assert isinstance(captured["context"], ssl.SSLContext)


def test_reserve_budget_402_raises_payment_required_without_retry(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=8,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout, context=None):
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
        client.reserve_budget(run_id="run-1", cost_limit=500)

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
        budget_max_retries=8,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout, context=None):
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
            input_tokens=11,
            cached_input_tokens=0,
            output_tokens=7,
            usage_breakdown=[
                {
                    "provider": "openai",
                    "provider_model_id": "gpt-4o-mini",
                    "input_tokens": 11,
                    "cached_input_tokens": 0,
                    "output_tokens": 7,
                }
            ],
        )

    assert attempts["count"] == 1
    assert sleeps == []
    assert excinfo.value.path == "/api/v1/settle"
    assert excinfo.value.status == 402
    assert isinstance(excinfo.value.__cause__, urllib.error.HTTPError)
    assert excinfo.value.__cause__.code == 402


def test_reserve_budget_409_raises_budget_exceeded_without_retry(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=8,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout, context=None):
        attempts["count"] += 1
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            hdrs=None,
            fp=io.BytesIO(
                b'{"error":"budget_exceeded","user_id":"alice","tokens_used":200,'
                b'"cost_used":17,"cost_limit":10}'
            ),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))

    with pytest.raises(BudgetExceededError) as excinfo:
        client.reserve_budget(run_id="run-1", cost_limit=500)

    assert attempts["count"] == 1
    assert sleeps == []
    assert excinfo.value.origin == "remote"
    assert excinfo.value.path == "/api/v1/reserve"
    assert excinfo.value.status_code == 409
    assert excinfo.value.user_id == "alice"
    assert excinfo.value.tokens_used == 200
    assert excinfo.value.cost_used == 17
    assert excinfo.value.cost_limit == 10
    assert isinstance(excinfo.value.__cause__, urllib.error.HTTPError)
    assert excinfo.value.__cause__.code == 409


def test_reserve_budget_401_remains_budget_transport_error(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=8,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout, context=None):
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
        client.reserve_budget(run_id="run-1", cost_limit=500)

    assert attempts["count"] == 1
    assert sleeps == []
    assert "status 401" in str(excinfo.value)


def test_settle_budget_409_raises_budget_exceeded_without_retry(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=8,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout, context=None):
        attempts["count"] += 1
        raise urllib.error.HTTPError(
            request.full_url,
            409,
            "Conflict",
            hdrs=None,
            fp=io.BytesIO(
                b'{"error":"budget_exceeded","user_id":"alice","tokens_used":18,'
                b'"cost_used":12,"cost_limit":10}'
            ),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))

    with pytest.raises(BudgetExceededError) as excinfo:
        client.settle_budget(
            reserve_id="res-123",
            input_tokens=11,
            cached_input_tokens=2,
            output_tokens=7,
            usage_breakdown=[
                {
                    "provider": "openai",
                    "provider_model_id": "gpt-4o-mini",
                    "input_tokens": 11,
                    "cached_input_tokens": 2,
                    "output_tokens": 7,
                }
            ],
        )

    assert attempts["count"] == 1
    assert sleeps == []
    assert excinfo.value.origin == "remote"
    assert excinfo.value.path == "/api/v1/settle"
    assert excinfo.value.status_code == 409
    assert excinfo.value.user_id == "alice"
    assert excinfo.value.tokens_used == 18
    assert excinfo.value.cost_used == 12
    assert excinfo.value.cost_limit == 10
    assert isinstance(excinfo.value.__cause__, urllib.error.HTTPError)
    assert excinfo.value.__cause__.code == 409


def test_reserve_budget_500_retries_before_budget_transport_error(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_timeout_s=10.0,
        budget_max_retries=2,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout, context=None):
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
        client.reserve_budget(run_id="run-1", cost_limit=500)

    assert attempts["count"] == 3
    assert sleeps == [0.2, 0.4]
    assert "HTTPError" in str(excinfo.value)


def test_reserve_budget_retry_backoff_respects_total_budget_timeout(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_timeout_s=0.25,
        budget_max_retries=2,
    )
    attempts = {"count": 0}
    sleeps: list[float] = []

    def fake_urlopen(request, timeout, context=None):
        attempts["count"] += 1
        raise urllib.error.URLError(TimeoutError(f"attempt={attempts['count']}"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr("random.uniform", lambda lower, upper: 0.0)
    monotonic_values = iter([0.0, 0.0, 0.0, 0.2, 0.25])
    monkeypatch.setattr("time.monotonic", lambda: next(monotonic_values))

    with pytest.raises(BudgetTransportError):
        client.reserve_budget(run_id="run-1", cost_limit=500)

    assert attempts["count"] == 2
    assert sleeps == [0.2]


def test_client_budget_guard_sets_and_clears_budget_state(monkeypatch):
    client = actguard.Client(gateway_url="https://gw.example", api_key="sk-test")
    assert get_run_state() is None
    assert get_budget_state() is None
    assert get_current_state() is None

    monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-guard")
    monkeypatch.setattr(client, "settle_budget", lambda **_: None)

    with client.run(run_id="run-budget-state"):
        with client.budget_guard(cost_limit=50, plan_key="pro") as guard:
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


def test_budget_guard_without_api_key_skips_remote_reporting(monkeypatch):
    client = actguard.Client()
    calls: list[str] = []

    def should_not_call(**_kwargs):
        calls.append("remote")
        raise AssertionError("reserve/settle should be skipped without api_key")

    monkeypatch.setattr(client, "reserve_budget", should_not_call)
    monkeypatch.setattr(client, "settle_budget", should_not_call)

    with client.run(run_id="run-local-only"):
        with client.budget_guard(cost_limit=50):
            budget_state = get_budget_state()
            assert budget_state is not None
            assert budget_state.reserve_id is None

    assert calls == []


def test_budget_guard_reserve_transport_failure_warns_and_degrades_open(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
    )

    def fail_reserve(**_kwargs):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(client, "reserve_budget", fail_reserve)
    monkeypatch.setattr(client, "settle_budget", lambda **_kwargs: None)

    with pytest.warns(RuntimeWarning) as recorded:
        with client.run(run_id="run-warn-reserve"):
            with client.budget_guard(cost_limit=50):
                budget_state = get_budget_state()
                assert budget_state is not None
                assert budget_state.reserve_id is None

    warning = recorded[0].message
    assert isinstance(warning.error, MonitoringDegradedError)
    assert warning.error.subsystem == "budget"
    assert warning.error.operation == "reserve"
    assert warning.error.failure_kind == "timeout"
    assert "TimeoutError: timed out" in str(warning)


def test_budget_guard_reserve_budget_exceeded_raises(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
    )

    monkeypatch.setattr(
        client,
        "reserve_budget",
        lambda **_kwargs: (_ for _ in ()).throw(
            BudgetExceededError(
                user_id="alice",
                tokens_used=200,
                cost_used=17,
                cost_limit=10,
                limit_type="cost",
                origin="remote",
                path="/api/v1/reserve",
                status_code=409,
            )
        ),
    )
    monkeypatch.setattr(client, "settle_budget", lambda **_kwargs: None)

    with pytest.raises(BudgetExceededError) as excinfo:
        with client.run(run_id="run-raise-reserve-budget"):
            with client.budget_guard(cost_limit=50):
                pass

    assert excinfo.value.path == "/api/v1/reserve"
    assert excinfo.value.status_code == 409
    assert get_current_state() is None


def test_budget_guard_reserve_payment_required_raises(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
    )

    monkeypatch.setattr(
        client,
        "reserve_budget",
        lambda **_kwargs: (_ for _ in ()).throw(
            ActGuardPaymentRequired(path="/api/v1/reserve")
        ),
    )
    monkeypatch.setattr(client, "settle_budget", lambda **_kwargs: None)

    with pytest.raises(ActGuardPaymentRequired) as excinfo:
        with client.run(run_id="run-raise-reserve-payment"):
            with client.budget_guard(cost_limit=50):
                pass

    assert excinfo.value.path == "/api/v1/reserve"
    assert get_current_state() is None


def test_budget_guard_settle_transport_failure_warns_without_raising(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
    )

    monkeypatch.setattr(client, "reserve_budget", lambda **_kwargs: "res-123")

    def fail_settle(**_kwargs):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(client, "settle_budget", fail_settle)

    with pytest.warns(RuntimeWarning) as recorded:
        with client.run(run_id="run-warn-settle"):
            with client.budget_guard(cost_limit=50):
                pass

    warning = recorded[0].message
    assert isinstance(warning.error, MonitoringDegradedError)
    assert warning.error.subsystem == "budget"
    assert warning.error.operation == "settle"
    assert warning.error.failure_kind == "connection"
    assert "ConnectionRefusedError: refused" in str(warning)
    assert get_current_state() is None


def test_budget_guard_settle_budget_exceeded_raises(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
    )

    monkeypatch.setattr(client, "reserve_budget", lambda **_kwargs: "res-123")
    monkeypatch.setattr(
        client,
        "settle_budget",
        lambda **_kwargs: (_ for _ in ()).throw(
            BudgetExceededError(
                user_id="alice",
                tokens_used=18,
                cost_used=12,
                cost_limit=10,
                limit_type="cost",
                origin="remote",
                path="/api/v1/settle",
                status_code=409,
            )
        ),
    )

    with pytest.raises(BudgetExceededError) as excinfo:
        with client.run(run_id="run-raise-settle-budget"):
            with client.budget_guard(cost_limit=50):
                pass

    assert excinfo.value.path == "/api/v1/settle"
    assert excinfo.value.status_code == 409
    assert get_current_state() is None


def test_budget_guard_settle_payment_required_raises(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
    )

    monkeypatch.setattr(client, "reserve_budget", lambda **_kwargs: "res-123")
    monkeypatch.setattr(
        client,
        "settle_budget",
        lambda **_kwargs: (_ for _ in ()).throw(
            ActGuardPaymentRequired(path="/api/v1/settle")
        ),
    )

    with pytest.raises(ActGuardPaymentRequired) as excinfo:
        with client.run(run_id="run-raise-settle-payment"):
            with client.budget_guard(cost_limit=50):
                pass

    assert excinfo.value.path == "/api/v1/settle"
    assert get_current_state() is None


def test_budget_guard_settle_http_failure_warning_includes_status_summary(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
    )

    monkeypatch.setattr(client, "reserve_budget", lambda **_kwargs: "res-123")
    monkeypatch.setattr(
        client,
        "settle_budget",
        lambda **_kwargs: (_ for _ in ()).throw(
            BudgetTransportError(
                "Budget API request failed with status 503 at /api/v1/settle.",
                status_code=503,
            )
        ),
    )

    with pytest.warns(RuntimeWarning) as recorded:
        with client.run(run_id="run-warn-settle-http"):
            with client.budget_guard(cost_limit=50):
                pass

    warning = recorded[0].message
    assert isinstance(warning.error, MonitoringDegradedError)
    assert warning.error.operation == "settle"
    assert warning.error.failure_kind == "http"
    assert "status 503" in str(warning)
    assert "/api/v1/settle" in str(warning)


def test_client_budget_guard_requires_active_run():
    client = actguard.Client()

    with pytest.raises(MissingRuntimeContextError):
        with client.budget_guard(cost_limit=50):
            pass


def test_client_budget_guard_inside_run_reuses_existing_run(monkeypatch):
    client = actguard.Client()
    monkeypatch.setattr(client, "reserve_budget", lambda **_: "res-shared")
    monkeypatch.setattr(client, "settle_budget", lambda **_: None)

    with client.run(run_id="run-existing", user_id="alice"):
        assert get_budget_state() is None
        with client.budget_guard(cost_limit=100):
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
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        event_mode="off",
    )
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
        with client.budget_guard(cost_limit=100, plan_key="pro") as guard:
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
    assert calls[0][1]["cost_limit"] == 100
    assert calls[0][1]["plan_key"] == "pro"
    assert calls[1][1] == {
        "reserve_id": "res_123",
        "input_tokens": 11,
        "cached_input_tokens": 2,
        "output_tokens": 7,
        "usage_breakdown": [
            {
                "provider": "openai",
                "provider_model_id": "gpt-4o-mini",
                "input_tokens": 11,
                "cached_input_tokens": 2,
                "output_tokens": 7,
            }
        ],
    }
    client.close()


def test_client_budget_guard_settle_preserves_per_call_usage_breakdown(monkeypatch):
    captured: list[tuple[str, dict]] = []

    class _Response:
        def __init__(self, body: bytes) -> None:
            self._body = body
            self.status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def getcode(self) -> int:
            return self.status

        def read(self) -> bytes:
            return self._body

    def fake_urlopen(request, timeout, context=None):
        captured.append((request.full_url, json.loads(request.data.decode())))
        if request.full_url.endswith("/api/v1/reserve"):
            return _Response(b'{"reserve_id":"res-multi"}')
        return _Response(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=0,
        event_mode="off",
    )

    with client.run(run_id="run-multi-settle"):
        with client.budget_guard(name="search_tool", cost_limit=100):
            record_usage(
                provider="openai",
                provider_model_id="gpt-4o",
                input_tokens=10,
                cached_input_tokens=1,
                output_tokens=5,
            )
            record_usage(
                provider="anthropic",
                provider_model_id="claude-sonnet-4",
                input_tokens=3,
                output_tokens=2,
            )
            record_usage(
                provider="openai",
                provider_model_id="gpt-4o",
                input_tokens=4,
                cached_input_tokens=1,
                output_tokens=1,
            )

    budget_requests = [
        (url, payload)
        for url, payload in captured
        if url.endswith("/api/v1/reserve") or url.endswith("/api/v1/settle")
    ]

    assert [url for url, _ in budget_requests] == [
        "https://gw.example/api/v1/reserve",
        "https://gw.example/api/v1/settle",
    ]
    assert budget_requests[1][1] == {
        "reserve_id": "res-multi",
        "input_tokens": 17,
        "cached_input_tokens": 2,
        "output_tokens": 8,
        "usage_breakdown": [
            {
                "provider": "openai",
                "provider_model_id": "gpt-4o",
                "input_tokens": 10,
                "cached_input_tokens": 1,
                "output_tokens": 5,
                "scope_name": "search_tool",
            },
            {
                "provider": "anthropic",
                "provider_model_id": "claude-sonnet-4",
                "input_tokens": 3,
                "cached_input_tokens": 0,
                "output_tokens": 2,
                "scope_name": "search_tool",
            },
            {
                "provider": "openai",
                "provider_model_id": "gpt-4o",
                "input_tokens": 4,
                "cached_input_tokens": 1,
                "output_tokens": 1,
                "scope_name": "search_tool",
            },
        ],
    }
    client.close()


def test_client_budget_guard_usage_breakdown_uses_active_scope_name(monkeypatch):
    captured: list[tuple[str, dict]] = []

    class _Response:
        def __init__(self, body: bytes) -> None:
            self._body = body
            self.status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def getcode(self) -> int:
            return self.status

        def read(self) -> bytes:
            return self._body

    def fake_urlopen(request, timeout, context=None):
        captured.append((request.full_url, json.loads(request.data.decode())))
        if request.full_url.endswith("/api/v1/reserve"):
            return _Response(b'{"reserve_id":"res-scope-name"}')
        return _Response(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        budget_max_retries=0,
        event_mode="off",
    )

    with client.run(run_id="run-scope-name-settle"):
        with client.budget_guard(cost_limit=100):
            record_usage(
                provider="openai",
                provider_model_id="gpt-4o",
                input_tokens=2,
                output_tokens=1,
            )
            with client.budget_guard(name="search_tool", cost_limit=50):
                record_usage(
                    provider="anthropic",
                    provider_model_id="claude-sonnet-4",
                    input_tokens=4,
                    cached_input_tokens=1,
                    output_tokens=3,
                )

    budget_requests = [
        (url, payload)
        for url, payload in captured
        if url.endswith("/api/v1/reserve") or url.endswith("/api/v1/settle")
    ]

    assert [url for url, _ in budget_requests] == [
        "https://gw.example/api/v1/reserve",
        "https://gw.example/api/v1/settle",
    ]
    assert budget_requests[1][1] == {
        "reserve_id": "res-scope-name",
        "input_tokens": 6,
        "cached_input_tokens": 1,
        "output_tokens": 4,
        "usage_breakdown": [
            {
                "provider": "openai",
                "provider_model_id": "gpt-4o",
                "input_tokens": 2,
                "cached_input_tokens": 0,
                "output_tokens": 1,
            },
            {
                "provider": "anthropic",
                "provider_model_id": "claude-sonnet-4",
                "input_tokens": 4,
                "cached_input_tokens": 1,
                "output_tokens": 3,
                "scope_name": "search_tool",
            },
        ],
    }
    client.close()


def test_client_budget_guard_settles_when_limit_is_exceeded(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        event_mode="off",
    )
    calls = []

    monkeypatch.setattr(
        client,
        "reserve_budget",
        lambda **kwargs: calls.append(("reserve", kwargs)) or "res-blocked",
    )
    monkeypatch.setattr(
        client,
        "settle_budget",
        lambda **kwargs: calls.append(("settle", kwargs)) or None,
    )

    raw = type(
        "UsageResponse",
        (),
        {
            "usage_metadata": {
                "input_tokens": 31,
                "output_tokens": 5,
                "input_token_details": {"cache_read": 7},
            },
            "response_metadata": {
                "model_name": "gpt-4o",
                "token_usage": {"prompt_tokens": 31, "completion_tokens": 5},
            },
        },
    )()

    with pytest.raises(BudgetExceededError):
        with client.run(run_id="run-blocked-settle", user_id="alice"):
            with client.budget_guard(name="search_tool", cost_limit=3):
                assert record_response_usage({"raw": raw}, provider="openai")

    assert [name for name, _ in calls] == ["reserve", "settle"]
    assert calls[1][1] == {
        "reserve_id": "res-blocked",
        "input_tokens": 31,
        "cached_input_tokens": 7,
        "output_tokens": 5,
        "usage_breakdown": [
            {
                "provider": "openai",
                "provider_model_id": "gpt-4o",
                "input_tokens": 31,
                "cached_input_tokens": 7,
                "output_tokens": 5,
                "scope_name": "search_tool",
            }
        ],
    }
    client.close()


def test_client_budget_guard_debug_mode_still_calls_real_settle(monkeypatch):
    captured: list[tuple[str, dict]] = []

    class _Response:
        def __init__(self, body: bytes) -> None:
            self._body = body
            self.status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def getcode(self) -> int:
            return self.status

        def read(self) -> bytes:
            return self._body

    def fake_urlopen(request, timeout, context=None):
        captured.append((request.full_url, json.loads(request.data.decode())))
        if request.full_url.endswith("/api/v1/reserve"):
            return _Response(b'{"reserve_id":"res-debug"}')
        return _Response(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        debug=True,
        budget_max_retries=0,
        event_mode="off",
    )

    with client.run(run_id="run-debug-settle"):
        with client.budget_guard(cost_limit=100):
            budget_state = get_budget_state()
            assert budget_state is not None
            budget_state.provider = "openai"
            budget_state.provider_model_id = "gpt-4o-mini"
            budget_state.input_tokens = 11
            budget_state.cached_input_tokens = 2
            budget_state.output_tokens = 7

    budget_requests = [
        (url, payload)
        for url, payload in captured
        if url.endswith("/api/v1/reserve") or url.endswith("/api/v1/settle")
    ]

    assert [url for url, _ in budget_requests] == [
        "https://gw.example/api/v1/reserve",
        "https://gw.example/api/v1/settle",
    ]
    assert budget_requests[0][1]["cost_limit"] == 100
    assert "user_id" not in budget_requests[0][1]
    assert budget_requests[1][1]["reserve_id"] == "res-debug"
    assert budget_requests[1][1]["input_tokens"] == 11
    assert budget_requests[1][1]["cached_input_tokens"] == 2
    assert budget_requests[1][1]["output_tokens"] == 7
    assert budget_requests[1][1]["usage_breakdown"] == [
        {
            "provider": "openai",
            "provider_model_id": "gpt-4o-mini",
            "input_tokens": 11,
            "cached_input_tokens": 2,
            "output_tokens": 7,
        }
    ]
    assert "run_id" not in budget_requests[1][1]
    client.close()


def test_budget_guard_no_leakage_between_clients(monkeypatch):
    client_a = actguard.Client(gateway_url="https://gw.example", api_key="key-a")
    client_b = actguard.Client(gateway_url="https://gw.example", api_key="key-b")
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
        with client_a.budget_guard(run_id="run-a", cost_limit=100):
            pass
    with client_b.run(run_id="run-b"):
        with client_b.budget_guard(run_id="run-b", cost_limit=100):
            pass

    assert calls_a[0][1]["run_id"] == "run-a"
    assert calls_a[0][1]["cost_limit"] == 100
    assert calls_b[0][1]["run_id"] == "run-b"
    assert calls_b[0][1]["cost_limit"] == 100


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


def test_event_client_ships_with_event_transport_config(monkeypatch):
    from actguard._config import ActGuardConfig
    from actguard.events.client import EventClient

    client = object.__new__(EventClient)
    client._config = ActGuardConfig(
        gateway_url="https://gw.example",
        api_key="sk-test",
        event_timeout_s=7.0,
        event_max_retries=2,
    )
    client._stop = __import__("threading").Event()
    captured = {"attempts": 0}
    sleeps: list[float] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def fake_urlopen(request, timeout, context=None):
        captured["attempts"] += 1
        captured["timeout"] = timeout
        captured["context"] = context
        if captured["attempts"] < 3:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda delay: sleeps.append(delay))
    monkeypatch.setattr("random.uniform", lambda lower, upper: 0.0)

    client._ship_with_retry([{"name": "evt"}])

    assert captured["attempts"] == 3
    assert captured["timeout"] == 7.0
    assert isinstance(captured["context"], ssl.SSLContext)
    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(0.2)
    assert sleeps[1] >= sleeps[0]


def test_event_client_http_gateway_omits_ssl_context(monkeypatch):
    from actguard._config import ActGuardConfig
    from actguard.events.client import EventClient

    client = object.__new__(EventClient)
    client._config = ActGuardConfig(
        gateway_url="http://localhost:8787",
        api_key="sk-test",
        event_timeout_s=7.0,
        event_max_retries=0,
    )
    client._stop = __import__("threading").Event()
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def fake_urlopen(request, timeout, context=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["context"] = context
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client._ship_with_retry([{"name": "evt"}])

    assert captured["url"] == "http://localhost:8787/api/v1/events"
    assert captured["timeout"] == 7.0
    assert captured["context"] is None


def test_event_client_close_wait_uses_event_timeout(monkeypatch):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        event_timeout_s=7.0,
    )
    assert client.event_client is not None
    seen = {}

    def fake_join(timeout):
        seen["timeout"] = timeout

    monkeypatch.setattr(client.event_client._thread, "join", fake_join)

    client.event_client.close(wait=True)

    assert seen == {"timeout": 8.0}

    client.close()
