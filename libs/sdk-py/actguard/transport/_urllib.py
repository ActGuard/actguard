from __future__ import annotations

import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import certifi


def urlopen(
    request: Any,
    *,
    timeout: float,
) -> Any:
    url = _request_url(request)
    context = _ssl_context_for_url(url)
    if context is None:
        return urllib.request.urlopen(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout, context=context)


def _request_url(request: Any) -> str:
    full_url = getattr(request, "full_url", None)
    if isinstance(full_url, str):
        return full_url
    return str(request)


def _ssl_context_for_url(url: str) -> ssl.SSLContext | None:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme != "https":
        return None
    return ssl.create_default_context(cafile=certifi.where())


class _TransportDebugTrace:
    def __init__(
        self,
        *,
        request: Any,
        timeout: float,
        attempt: int | None,
        max_attempts: int | None,
    ) -> None:
        self._request = request
        self._timeout = timeout
        self._attempt = attempt
        self._max_attempts = max_attempts
        self._method = _request_method(request)
        self._url = _request_url(request)
        self._started_at = time.perf_counter()

    def log_request(self) -> None:
        _debug_write(
            "request",
            self._prefix(),
            self._method,
            self._url,
            f"timeout={self._timeout:.3f}s",
        )
        body = _format_body(getattr(self._request, "data", None))
        if body is not None:
            _debug_write("request-body", body)

    def log_success(
        self,
        *,
        response: Any,
        body: bytes | None,
    ) -> None:
        duration_ms = (time.perf_counter() - self._started_at) * 1000.0
        status = _response_status(response)
        status_text = f"status={status}" if status is not None else "status=unknown"
        _debug_write(
            "response",
            self._prefix(),
            self._method,
            self._url,
            status_text,
            f"duration_ms={duration_ms:.1f}",
        )
        rendered_body = _format_body(body)
        if rendered_body is not None:
            _debug_write("response-body", rendered_body)

    def log_failure(self, *, exc: BaseException) -> None:
        duration_ms = (time.perf_counter() - self._started_at) * 1000.0
        parts = [
            "error",
            self._prefix(),
            self._method,
            self._url,
        ]
        status = getattr(exc, "code", None)
        if isinstance(status, int):
            parts.append(f"status={status}")
        parts.append(f"duration_ms={duration_ms:.1f}")
        parts.append(f"error={_exception_summary(exc)}")
        _debug_write(*parts)

    def _prefix(self) -> str:
        if self._attempt is not None and self._max_attempts is not None:
            return f"attempt={self._attempt}/{self._max_attempts}"
        if self._attempt is not None:
            return f"attempt={self._attempt}"
        return "attempt=1"


def _request_method(request: Any) -> str:
    getter = getattr(request, "get_method", None)
    if callable(getter):
        return str(getter())
    method = getattr(request, "method", None)
    if isinstance(method, str) and method:
        return method
    return "GET"


def _response_status(response: Any) -> int | None:
    getter = getattr(response, "getcode", None)
    if callable(getter):
        status = getter()
        if isinstance(status, int):
            return status
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status
    return None


def _exception_summary(exc: BaseException) -> str:
    current = _deepest_exception(exc)
    message = str(current).strip()
    if message:
        return _redact_text(f"{type(current).__name__}: {message}")
    return type(current).__name__


def _format_body(body: Any) -> str | None:
    if body is None:
        return None
    if isinstance(body, bytearray):
        body = bytes(body)
    if isinstance(body, bytes):
        try:
            decoded = body.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary {len(body)} bytes>"
        return _format_text_body(decoded)
    if isinstance(body, str):
        return _format_text_body(body)
    return _redact_text(str(body))


def _format_text_body(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except Exception:
        return _redact_text(stripped)
    return json.dumps(_redact_value(parsed), sort_keys=True, separators=(",", ":"))


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            current_key: _redact_value(current_value, key=current_key)
            for current_key, current_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        if key is not None and key.lower() in {"authorization", "api_key", "x-api-key"}:
            if key.lower() == "authorization":
                return "Bearer <redacted>"
            return "<redacted>"
        return _redact_text(value)
    return value


def _redact_text(text: str) -> str:
    return re.sub(r"(?i)\bbearer\s+\S+", "Bearer <redacted>", text)


def _deepest_exception(exc: BaseException) -> BaseException:
    current = exc
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        next_error = getattr(current, "__cause__", None)
        if next_error is None:
            next_error = getattr(current, "cause", None)
        if next_error is None and isinstance(current, urllib.error.URLError):
            reason = current.reason
            if isinstance(reason, BaseException):
                next_error = reason
        if next_error is None:
            return current
        current = next_error
    return current


def _debug_write(*parts: str) -> None:
    filtered = [part for part in parts if part]
    if not filtered:
        return
    print("[actguard debug]", *filtered, file=sys.stderr)


def start_debug_trace(
    *,
    request: Any,
    timeout: float,
    debug: bool,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> _TransportDebugTrace | None:
    if not debug:
        return None
    trace = _TransportDebugTrace(
        request=request,
        timeout=timeout,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    trace.log_request()
    return trace
