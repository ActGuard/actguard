from __future__ import annotations

import base64
import json
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from contextvars import Token
from pathlib import Path
from typing import Any, Mapping, Optional

from actguard._config import ActGuardConfig
from actguard.core.run_context import (
    RunState,
    get_run_state,
    reset_run_state,
    set_run_state,
)
from actguard.events.client import EventClient


class _ClientRunContext:
    """Context manager installed by Client.run(...)."""

    def __init__(
        self,
        *,
        client: "Client",
        user_id: Optional[str],
        run_id: Optional[str],
    ) -> None:
        self.client = client
        self.run_id = run_id if run_id is not None else str(uuid.uuid4())
        self.user_id = user_id
        self._state: Optional[RunState] = None
        self._token: Optional[Token] = None

    def __enter__(self) -> "_ClientRunContext":
        from actguard.exceptions import NestedRunContextError

        active = get_run_state()
        if active is not None:
            raise NestedRunContextError(
                "Nested runtime contexts are not supported. "
                f"Active run_id={active.run_id!r}; finish the current client.run(...) "
                "before entering another."
            )

        self._state = RunState(
            client=self.client,
            run_id=self.run_id,
            user_id=self.user_id,
        )
        self._token = set_run_state(self._state)
        try:
            from actguard.reporting import emit_event

            emit_event(
                "run",
                "start",
                {
                    "run_id": self._state.run_id,
                    "user_id": self._state.user_id,
                },
            )
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        run_id = self.run_id
        if self._state is not None:
            run_id = self._state.run_id

        try:
            from actguard.reporting import emit_event

            if exc_type is None:
                emit_event(
                    "run",
                    "end",
                    {"run_id": run_id},
                    outcome="success",
                )
            else:
                from actguard.exceptions import ActGuardViolation

                if issubclass(exc_type, ActGuardViolation):
                    emit_event(
                        "run",
                        "end",
                        {"run_id": run_id, "error_type": exc_type.__name__},
                        severity="error",
                        outcome="blocked",
                    )
                else:
                    emit_event(
                        "run",
                        "end",
                        {"run_id": run_id, "error_type": exc_type.__name__},
                        severity="error",
                        outcome="failed",
                    )
        except Exception:
            pass

        if self._token is not None:
            reset_run_state(self._token)
            self._token = None
        self._state = None

    async def __aenter__(self) -> "_ClientRunContext":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return self.__exit__(exc_type, exc_val, exc_tb)

    def get_attempt_count(self, tool_id: str) -> int:
        if self._state is None:
            return 0
        return self._state.get_attempt_count(tool_id)


class Client:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        gateway_url: Optional[str] = None,
        event_mode: str = "verbose",
        flush_interval_ms: int = 1000,
        max_batch_events: int = 100,
        max_batch_bytes: int = 256_000,
        max_queue_events: int = 10_000,
        timeout_s: float = 5.0,
        max_retries: int = 8,
        backoff_base_ms: int = 200,
        backoff_max_ms: int = 10_000,
    ) -> None:
        self.api_key = api_key
        self.gateway_url = gateway_url
        self.event_mode = event_mode
        self.flush_interval_ms = flush_interval_ms
        self.max_batch_events = max_batch_events
        self.max_batch_bytes = max_batch_bytes
        self.max_queue_events = max_queue_events
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_base_ms = backoff_base_ms
        self.backoff_max_ms = backoff_max_ms

        self._config = ActGuardConfig(
            gateway_url=gateway_url,
            api_key=api_key,
            event_mode=event_mode,
            flush_interval_ms=flush_interval_ms,
            max_batch_events=max_batch_events,
            max_batch_bytes=max_batch_bytes,
            max_queue_events=max_queue_events,
            timeout_s=timeout_s,
            max_retries=max_retries,
            backoff_base_ms=backoff_base_ms,
            backoff_max_ms=backoff_max_ms,
        )
        self._event_client: Optional[EventClient]
        if self._config.events_enabled:
            self._event_client = EventClient(self._config)
        else:
            self._event_client = None

    @property
    def event_client(self) -> Optional[EventClient]:
        return self._event_client

    @property
    def reporting_config(self) -> ActGuardConfig:
        return self._config

    def run(
        self,
        user_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> _ClientRunContext:
        return _ClientRunContext(client=self, user_id=user_id, run_id=run_id)

    def budget_guard(
        self,
        *,
        user_id: Optional[str] = None,
        name: Optional[str] = None,
        usd_limit: Optional[float] = None,
        run_id: Optional[str] = None,
        plan_key: Optional[str] = None,
    ):
        from actguard.budget import BudgetGuard

        return BudgetGuard(
            client=self,
            user_id=user_id,
            name=name,
            usd_limit=usd_limit,
            run_id=run_id,
            plan_key=plan_key,
        )

    def _post_budget_api(
        self, *, path: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        from actguard.exceptions import BudgetPaymentRequiredError, BudgetTransportError

        if not self.gateway_url:
            raise BudgetTransportError(
                "Client.gateway_url is required for budget reserve/settle APIs."
            )
        if not self.api_key:
            raise BudgetTransportError(
                "Client.api_key is required for budget reserve/settle APIs."
            )

        body = json.dumps(payload).encode()
        url = self.gateway_url.rstrip("/") + path
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout_s) as response:
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
                    raise BudgetPaymentRequiredError(path=path, status=status) from exc
                if status in (400, 401, 403, 422):
                    raise BudgetTransportError(
                        f"Budget API request failed with status {status} at {path}."
                    ) from exc
                last_error = exc
            except Exception as exc:
                last_error = exc

            if attempt < self.max_retries:
                jitter = random.uniform(0, self.backoff_base_ms)
                delay_ms = min(
                    self.backoff_base_ms * (2**attempt) + jitter,
                    self.backoff_max_ms,
                )
                time.sleep(delay_ms / 1000.0)

        from actguard.exceptions import BudgetTransportError

        raise BudgetTransportError(
            f"Budget API request failed at {path}: {type(last_error).__name__}"
        ) from last_error

    def reserve_budget(
        self,
        *,
        run_id: str,
        usd_limit_micros: Optional[int],
        plan_key: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        from actguard.exceptions import BudgetTransportError

        payload = {
            "run_id": run_id,
        }
        if usd_limit_micros is not None:
            payload["usd_limit_micros"] = usd_limit_micros
        if plan_key:
            payload["plan_key"] = plan_key
        if user_id:
            payload["user_id"] = user_id
        response = self._post_budget_api(path="/api/v1/reserve", payload=payload)
        reserve_id = response.get("reserve_id")
        if not isinstance(reserve_id, str) or not reserve_id:
            raise BudgetTransportError(
                "Reserve response missing required 'reserve_id'."
            )
        return reserve_id

    def settle_budget(
        self,
        *,
        reserve_id: str,
        provider: str,
        provider_model_id: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> None:
        payload = {
            "reserve_id": reserve_id,
            "provider": provider,
            "provider_model_id": provider_model_id,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
        }
        self._post_budget_api(path="/api/v1/settle", payload=payload)

    def close(self) -> None:
        if self._event_client is None:
            return
        try:
            # Shutdown should drain the queue so terminal events like run.end
            # are not lost when callers close the client before process exit.
            self._event_client.close(wait=True)
        finally:
            self._event_client = None

    @classmethod
    def from_file(cls, path: str | os.PathLike[str]) -> "Client":
        with Path(path).open(encoding="utf-8") as handle:
            data = json.load(handle)
        return cls._from_mapping(data)

    @classmethod
    def from_env(cls) -> "Client":
        raw = os.environ.get("ACTGUARD_CONFIG")
        if raw is None:
            return cls()

        try:
            decoded = base64.b64decode(raw).decode()
            data = json.loads(decoded)
        except Exception:
            with Path(raw).open(encoding="utf-8") as handle:
                data = json.load(handle)

        return cls._from_mapping(data)

    @classmethod
    def _from_mapping(cls, data: Mapping[str, Any]) -> "Client":
        return cls(
            api_key=data.get("api_key"),
            gateway_url=data.get("gateway_url"),
            event_mode=data.get("event_mode", "verbose"),
            flush_interval_ms=data.get("flush_interval_ms", 1000),
            max_batch_events=data.get("max_batch_events", 100),
            max_batch_bytes=data.get("max_batch_bytes", 256_000),
            max_queue_events=data.get("max_queue_events", 10_000),
            timeout_s=data.get("timeout_s", 5.0),
            max_retries=data.get("max_retries", 8),
            backoff_base_ms=data.get("backoff_base_ms", 200),
            backoff_max_ms=data.get("backoff_max_ms", 10_000),
        )
