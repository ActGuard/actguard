from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional

from actguard._config import ActGuardConfig
from actguard.transport._urllib import start_debug_trace, urlopen


class BudgetTransport:
    """HTTP transport for reserve/settle budget API calls."""

    def __init__(self, config: ActGuardConfig) -> None:
        self._config = config

    def post(self, *, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._request_json(
            method="POST",
            path=path,
            payload=payload,
            require_auth=True,
        )

    def get_public(self, *, path: str) -> Mapping[str, Any]:
        return self._request_json(
            method="GET",
            path=path,
            payload=None,
            require_auth=False,
        )

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        payload: Optional[Mapping[str, Any]],
        require_auth: bool,
    ) -> Mapping[str, Any]:
        from actguard.exceptions import (
            BudgetTransportError,
        )

        if not self._config.gateway_url:
            raise BudgetTransportError(
                "Client.gateway_url is required for budget reserve/settle APIs."
            )
        if require_auth and not self._config.api_key:
            raise BudgetTransportError(
                "Client.api_key is required for budget reserve/settle APIs."
            )

        body = json.dumps(payload).encode() if payload is not None else None
        url = self._config.gateway_url.rstrip("/") + path
        headers = {"Content-Type": "application/json"}
        if require_auth:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        deadline = time.monotonic() + self._config.budget_timeout_s
        last_error: Optional[Exception] = None
        for attempt in range(self._config.budget_max_retries + 1):
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0 and attempt > 0:
                break
            timeout_s = max(remaining_s, 0.001)
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers=headers,
                    method=method,
                )
                trace = start_debug_trace(
                    request=req,
                    timeout=timeout_s,
                    debug=self._config.debug,
                    attempt=attempt + 1,
                    max_attempts=self._config.budget_max_retries + 1,
                )
                with urlopen(req, timeout=timeout_s) as response:
                    raw = response.read()
                if trace is not None:
                    trace.log_success(response=response, body=raw)
                if not raw:
                    return {}
                parsed = json.loads(raw.decode())
                if isinstance(parsed, dict):
                    return parsed
                return {}
            except urllib.error.HTTPError as exc:
                if trace is not None:
                    trace.log_failure(exc=exc)
                status = exc.code
                if status == 402:
                    raise _payment_required_error(
                        path=path,
                        status=status,
                        exc=exc,
                    ) from exc
                if status == 409:
                    raise _budget_limit_exceeded_error(
                        path=path,
                        status=status,
                        exc=exc,
                    ) from exc
                if status in (400, 401, 403, 422):
                    raise BudgetTransportError(
                        _budget_http_error_message(
                            status=status,
                            path=path,
                            exc=exc,
                        ),
                        cause=exc,
                        status_code=status,
                    ) from exc
                last_error = exc
            except Exception as exc:
                if trace is not None:
                    trace.log_failure(exc=exc)
                from actguard._monitoring import (
                    SSL_CERT_FIX_MESSAGE,
                    _is_ssl_cert_error,
                )

                if _is_ssl_cert_error(exc):
                    raise BudgetTransportError(
                        SSL_CERT_FIX_MESSAGE, cause=exc
                    ) from exc
                last_error = exc

            if attempt < self._config.budget_max_retries:
                jitter = random.uniform(0, self._config.backoff_base_ms)
                delay_ms = min(
                    self._config.backoff_base_ms * (2**attempt) + jitter,
                    self._config.backoff_max_ms,
                )
                sleep_s = min(delay_ms / 1000.0, max(deadline - time.monotonic(), 0.0))
                if sleep_s <= 0:
                    break
                time.sleep(sleep_s)

        from actguard.exceptions import BudgetTransportError

        raise BudgetTransportError(
            f"Budget API request failed at {path}: {type(last_error).__name__}",
            cause=last_error,
        ) from last_error


def _budget_http_error_message(
    *,
    status: int,
    path: str,
    exc: urllib.error.HTTPError,
) -> str:
    detail = _http_error_detail(exc)
    if detail:
        return f"Budget API request failed with status {status} at {path}: {detail}"
    return f"Budget API request failed with status {status} at {path}."


def _budget_limit_exceeded_error(
    *,
    path: str,
    status: int,
    exc: urllib.error.HTTPError,
):
    from actguard.exceptions import BudgetExceededError

    parsed = _http_error_payload(exc)
    detail = _http_error_detail_from_payload(parsed)
    user_id = None
    tokens_used = 0
    cost_used = 0
    cost_limit = None

    if isinstance(parsed, dict):
        raw_user_id = parsed.get("user_id")
        if isinstance(raw_user_id, str) and raw_user_id:
            user_id = raw_user_id
        raw_tokens_used = parsed.get("tokens_used")
        if isinstance(raw_tokens_used, int):
            tokens_used = raw_tokens_used
        raw_cost_used = parsed.get("cost_used")
        if isinstance(raw_cost_used, int):
            cost_used = raw_cost_used
        raw_cost_limit = parsed.get("cost_limit")
        if isinstance(raw_cost_limit, int):
            cost_limit = raw_cost_limit

    error = BudgetExceededError(
        user_id=user_id,
        tokens_used=tokens_used,
        cost_used=cost_used,
        cost_limit=cost_limit,
        limit_type="cost",
        origin="remote",
        path=path,
        status_code=status,
        cause=exc,
    )
    if detail:
        error.details["summary"] = detail
    return error


def _payment_required_error(
    *,
    path: str,
    status: int,
    exc: urllib.error.HTTPError,
):
    from actguard.exceptions import ActGuardPaymentRequired

    parsed = _http_error_payload(exc)
    payload = parsed if isinstance(parsed, dict) else None
    detail = _http_error_detail_from_payload(parsed)

    return ActGuardPaymentRequired(
        path=path,
        status=status,
        user_message=_first_http_error_string(
            payload,
            "Message",
            "message",
            "detail",
            "error",
        )
        or detail,
        current_balance=_http_error_int(payload, "CurrentBalance", "current_balance"),
        required_amount=_http_error_int(
            payload, "RequiredAmount", "required_amount"
        ),
        shortfall=_http_error_int(payload, "Shortfall", "shortfall"),
        topup_url=_http_error_string(payload, "TopupURL", "topup_url"),
        topup_session_id=_http_error_string(
            payload, "TopupSessionID", "topup_session_id"
        ),
        user_id=_http_error_string(payload, "UserID", "user_id"),
        response_payload=payload,
        cause=exc,
    )


def _http_error_detail(exc: urllib.error.HTTPError) -> str | None:
    return _http_error_detail_from_payload(_http_error_payload(exc))


def _http_error_payload(exc: urllib.error.HTTPError) -> object | None:
    try:
        raw = exc.read()
    except Exception:
        return None
    if not raw:
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    stripped = " ".join(decoded.strip().split())
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return stripped


def _normalize_http_error_payload(payload: Mapping[str, Any]) -> str | None:
    try:
        rendered = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    except Exception:
        return None
    stripped = " ".join(rendered.strip().split())
    return stripped or None


def _http_error_detail_from_payload(payload: object | None) -> str | None:
    if isinstance(payload, dict):
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return _normalize_http_error_payload(payload)

    if isinstance(payload, str):
        stripped = " ".join(payload.strip().split())
        return stripped or None
    return None


def _http_error_string(
    payload: Mapping[str, Any] | None,
    *keys: str,
) -> str | None:
    if payload is None:
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def _first_http_error_string(
    payload: Mapping[str, Any] | None,
    *keys: str,
) -> str | None:
    return _http_error_string(payload, *keys)


def _http_error_int(
    payload: Mapping[str, Any] | None,
    *keys: str,
) -> int | None:
    if payload is None:
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None
