from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional

from actguard._config import ActGuardConfig


class BudgetTransport:
    """HTTP transport for reserve/settle budget API calls."""

    def __init__(self, config: ActGuardConfig) -> None:
        self._config = config

    def post(self, *, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        from actguard.exceptions import ActGuardPaymentRequired, BudgetTransportError

        if not self._config.gateway_url:
            raise BudgetTransportError(
                "Client.gateway_url is required for budget reserve/settle APIs."
            )
        if not self._config.api_key:
            raise BudgetTransportError(
                "Client.api_key is required for budget reserve/settle APIs."
            )

        body = json.dumps(payload).encode()
        url = self._config.gateway_url.rstrip("/") + path
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        deadline = time.monotonic() + self._config.budget_timeout_s
        last_error: Optional[Exception] = None
        for attempt in range(self._config.budget_max_retries + 1):
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0 and attempt > 0:
                break
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(
                    req, timeout=max(remaining_s, 0.001)
                ) as response:
                    raw = response.read()
                if not raw:
                    return {}
                parsed = json.loads(raw.decode())
                if isinstance(parsed, dict):
                    return parsed
                return {}
            except urllib.error.HTTPError as exc:
                status = exc.code
                if status == 402:
                    raise ActGuardPaymentRequired(
                        path=path, status=status, cause=exc
                    ) from exc
                if status in (400, 401, 403, 422):
                    raise BudgetTransportError(
                        f"Budget API request failed with status {status} at {path}.",
                        cause=exc,
                        status_code=status,
                    ) from exc
                last_error = exc
            except Exception as exc:
                from actguard._monitoring import SSL_CERT_FIX_MESSAGE, _is_ssl_cert_error

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
