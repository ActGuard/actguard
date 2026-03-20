from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

import pytest

import actguard
from actguard import _debug as debug_utils
from actguard._config import ActGuardConfig
from actguard.events.client import EventClient
from actguard.transport import _urllib as urllib_transport


class _FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self._body


@pytest.fixture(autouse=True)
def reset_actguard_debug_handler():
    logger = logging.getLogger("actguard")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers = [
        handler
        for handler in logger.handlers
        if not getattr(handler, "_actguard_debug_handler", False)
    ]
    try:
        yield
    finally:
        logger.handlers = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate


def test_client_debug_flag_propagates_to_config_and_file_loading(tmp_path):
    config_file = tmp_path / "actguard.json"
    config_file.write_text(
        json.dumps(
            {
                "gateway_url": "https://api.actguard.io",
                "api_key": "sk-test",
                "debug": True,
            }
        )
    )

    direct = actguard.Client(debug=True)
    from_file = actguard.Client.from_file(config_file)

    assert direct.debug is True
    assert direct.reporting_config.debug is True
    assert from_file.debug is True
    assert from_file.reporting_config.debug is True


def test_debug_mode_logs_budget_request_and_response(monkeypatch, capsys):
    client = actguard.Client(
        gateway_url="https://gw.example",
        api_key="sk-test",
        debug=True,
        budget_max_retries=0,
    )

    def fake_urlopen(request, timeout, context=None):
        return _FakeResponse(b'{"reserve_id":"res-123"}', status=200)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    reserve_id = client.reserve_budget(run_id="run-1", usd_limit_micros=500_000)

    captured = capsys.readouterr()
    stderr = captured.err
    assert reserve_id == "res-123"
    assert "request attempt=1/1 POST https://gw.example/api/v1/reserve" in stderr
    assert '"run_id":"run-1"' in stderr
    assert '"usd_limit_micros":500000' in stderr
    assert (
        "response attempt=1/1 POST https://gw.example/api/v1/reserve status=200"
        in stderr
    )
    assert '"reserve_id":"res-123"' in stderr
    assert "Bearer sk-test" not in stderr


def test_debug_mode_logs_event_retry_attempts(monkeypatch, capsys):
    client = object.__new__(EventClient)
    client._config = ActGuardConfig(
        gateway_url="https://gw.example",
        api_key="sk-test",
        debug=True,
        event_timeout_s=7.0,
        event_max_retries=1,
    )
    client._stop = threading.Event()

    attempts = {"count": 0}

    def fake_urlopen(request, timeout, context=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return _FakeResponse(b"", status=202)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("random.uniform", lambda lower, upper: 0.0)

    client._ship_with_retry([{"event": "test"}])

    captured = capsys.readouterr()
    stderr = captured.err
    assert "request attempt=1/2 POST https://gw.example/api/v1/events" in stderr
    assert "error attempt=1/2 POST https://gw.example/api/v1/events" in stderr
    assert "TimeoutError: timed out" in stderr
    assert "request attempt=2/2 POST https://gw.example/api/v1/events" in stderr
    assert (
        "response attempt=2/2 POST https://gw.example/api/v1/events status=202"
        in stderr
    )
    assert '"event":"test"' in stderr


def test_debug_mode_redacts_sensitive_body_values(monkeypatch, capsys):
    request = urllib.request.Request(
        "https://gw.example/api/v1/debug",
        data=json.dumps(
            {
                "api_key": "sk-secret",
                "nested": {
                    "authorization": "Bearer super-secret",
                },
            }
        ).encode(),
        method="POST",
    )

    def fake_urlopen(request, timeout, context=None):
        return _FakeResponse(b"{}", status=204)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    trace = urllib_transport.start_debug_trace(
        request=request,
        timeout=1.0,
        debug=True,
    )
    with urllib_transport.urlopen(request, timeout=1.0) as response:
        if trace is not None:
            trace.log_success(response=response, body=None)
        pass

    captured = capsys.readouterr()
    stderr = captured.err
    assert "<redacted>" in stderr
    assert "Bearer super-secret" not in stderr
    assert "sk-secret" not in stderr


def test_debug_mode_off_emits_no_console_output(monkeypatch, capsys):
    request = urllib.request.Request(
        "https://gw.example/api/v1/debug",
        data=b'{"ok":true}',
        method="POST",
    )

    def fake_urlopen(request, timeout, context=None):
        return _FakeResponse(b'{"ok":true}', status=200)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with urllib_transport.urlopen(request, timeout=1.0):
        pass

    captured = capsys.readouterr()
    assert captured.err == ""


def test_transport_debug_uses_color_when_enabled(monkeypatch, capsys):
    request = urllib.request.Request(
        "https://gw.example/api/v1/debug",
        data=b'{"ok":true}',
        method="POST",
    )

    monkeypatch.setattr(debug_utils, "use_color", lambda stream=None: True)

    trace = urllib_transport.start_debug_trace(
        request=request,
        timeout=1.0,
        debug=True,
    )
    assert trace is not None

    captured = capsys.readouterr()
    assert "\x1b[" in captured.err
    assert "[actguard debug]" in captured.err
    assert "request" in captured.err


def test_client_debug_installs_one_actguard_handler():
    actguard.Client(debug=True, event_mode="off")
    actguard.Client(debug=True, event_mode="off")

    logger = logging.getLogger("actguard")
    handlers = [
        handler
        for handler in logger.handlers
        if getattr(handler, "_actguard_debug_handler", False)
    ]
    assert len(handlers) == 1
    assert logger.level == logging.DEBUG
    assert logger.propagate is False


def test_client_debug_logger_output_is_colorized(monkeypatch, capsys):
    monkeypatch.setattr(debug_utils, "use_color", lambda stream=None: True)
    actguard.Client(debug=True, event_mode="off")

    logger = logging.getLogger("actguard.integrations.openai")
    logger.debug("hello debug")

    captured = capsys.readouterr()
    assert "\x1b[" in captured.err
    assert "[actguard]" in captured.err
    assert "DEBUG" in captured.err
    assert "hello debug" in captured.err
