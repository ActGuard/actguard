import importlib

import pytest

import actguard
from actguard.exceptions import CircuitBreakerConfigurationError, CircuitOpenError
from actguard.tools.circuit_breaker import (
    FAIL_ON_DEFAULT,
    FailureKind,
    circuit_breaker,
)


class _Result:
    def __init__(self, status_code):
        self.status_code = status_code


class _HttpError(Exception):
    def __init__(self, status_code):
        self.response = _Result(status_code)


class _GrpcCode:
    def __init__(self, name):
        self.name = name


class _GrpcError(Exception):
    def __init__(self, code_name):
        self._code = _GrpcCode(code_name)

    def code(self):
        return self._code


class _CloudError(Exception):
    def __init__(self, code, status):
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


def test_default_fail_kinds_open_circuit():
    calls = {"n": 0}

    @circuit_breaker(name="dep", max_fails=2, reset_timeout=60)
    def fn(mode):
        calls["n"] += 1
        if mode == "transport":
            raise ConnectionError("conn refused")
        if mode == "timeout":
            raise TimeoutError("deadline")
        if mode == "overloaded":
            return _Result(503)
        return "ok"

    with pytest.raises(ConnectionError):
        fn("transport")
    with pytest.raises(TimeoutError):
        fn("timeout")

    with pytest.raises(CircuitOpenError):
        fn("overloaded")

    assert calls["n"] == 2


def test_default_ignore_kinds_do_not_open():
    @circuit_breaker(name="dep", max_fails=1, reset_timeout=60)
    def fn(status):
        return _Result(status)

    assert fn(400).status_code == 400
    assert fn(404).status_code == 404
    assert fn(409).status_code == 409


def test_custom_fail_on_set_union_adds_auth():
    @circuit_breaker(
        name="auth_dep",
        max_fails=1,
        fail_on=FAIL_ON_DEFAULT | {FailureKind.AUTH},
    )
    def fn():
        return _Result(401)

    fn()
    with pytest.raises(CircuitOpenError):
        fn()


def test_reset_timeout_short_circuit_and_recovery(monkeypatch):
    now = [1000.0]
    cb_mod = importlib.import_module("actguard.tools.circuit_breaker")
    monkeypatch.setattr(cb_mod.time, "time", lambda: now[0])

    calls = {"n": 0}
    should_fail = {"v": True}

    @circuit_breaker(name="dep", max_fails=1, reset_timeout=10)
    def fn():
        calls["n"] += 1
        if should_fail["v"]:
            raise ConnectionError("down")
        return "ok"

    with pytest.raises(ConnectionError):
        fn()

    now[0] = 1005.0
    with pytest.raises(CircuitOpenError) as exc_info:
        fn()
    assert exc_info.value.dependency_name == "dep"
    assert exc_info.value.reset_at == 1010.0
    assert calls["n"] == 1

    now[0] = 1011.0
    should_fail["v"] = False
    assert fn() == "ok"
    assert calls["n"] == 2


def test_http_status_classification():
    @circuit_breaker(name="dep", max_fails=10, reset_timeout=60)
    def fn(status):
        return _Result(status)

    assert fn(503).status_code == 503
    assert fn(429).status_code == 429
    assert fn(401).status_code == 401
    assert fn(403).status_code == 403
    assert fn(404).status_code == 404
    assert fn(400).status_code == 400


def test_grpc_code_classification_unavailable_counts_as_transport():
    @circuit_breaker(name="grpc", max_fails=1, reset_timeout=60)
    def fn():
        raise _GrpcError("UNAVAILABLE")

    with pytest.raises(_GrpcError):
        fn()

    with pytest.raises(CircuitOpenError):
        fn()


def test_cloud_response_classification_throttled_not_default_fail_on():
    @circuit_breaker(name="cloud", max_fails=1, reset_timeout=60)
    def fn():
        raise _CloudError("ThrottlingException", 400)

    with pytest.raises(_CloudError):
        fn()
    with pytest.raises(_CloudError):
        fn()


def test_unknown_exception_does_not_open_by_default():
    calls = {"n": 0}

    @circuit_breaker(name="dep", max_fails=2, reset_timeout=60)
    def fn(kind):
        calls["n"] += 1
        if kind == "transport":
            raise ConnectionError("conn")
        if kind == "unknown":
            raise RuntimeError("mystery")
        return "ok"

    with pytest.raises(ConnectionError):
        fn("transport")
    with pytest.raises(RuntimeError):
        fn("unknown")
    with pytest.raises(ConnectionError):
        fn("transport")
    with pytest.raises(ConnectionError):
        fn("transport")
    with pytest.raises(CircuitOpenError):
        fn("ok")

    assert calls["n"] == 4


def test_validation_rules():
    with pytest.raises(CircuitBreakerConfigurationError, match="name"):

        @circuit_breaker(name="")
        def bad_name():
            return None

    with pytest.raises(CircuitBreakerConfigurationError, match="max_fails"):

        @circuit_breaker(name="dep", max_fails=0)
        def bad_max_fails():
            return None

    with pytest.raises(CircuitBreakerConfigurationError, match="reset_timeout"):

        @circuit_breaker(name="dep", reset_timeout=0)
        def bad_reset_timeout():
            return None

    with pytest.raises(CircuitBreakerConfigurationError, match="disjoint"):

        @circuit_breaker(
            name="dep",
            fail_on={FailureKind.TIMEOUT},
            ignore_on={FailureKind.TIMEOUT},
        )
        def bad_overlap():
            return None

    with pytest.raises(CircuitBreakerConfigurationError, match="FailureKind"):

        @circuit_breaker(name="dep", fail_on={"timeout"})
        def bad_fail_on_type():
            return None


@pytest.mark.asyncio
async def test_async_behavior_parity():
    @circuit_breaker(name="async_dep", max_fails=1, reset_timeout=60)
    async def fn():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        await fn()

    with pytest.raises(CircuitOpenError):
        await fn()


def test_unified_tool_decorator_with_circuit_breaker():
    call_count = {"n": 0}

    @actguard.tool(circuit_breaker={"name": "tool_dep", "max_fails": 1})
    def fn():
        call_count["n"] += 1
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        fn()
    with pytest.raises(CircuitOpenError):
        fn()

    assert call_count["n"] == 1
