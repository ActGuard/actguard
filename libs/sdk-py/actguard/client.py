from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from actguard._config import ActGuardConfig
from actguard.core.runtime import ClientRunContext
from actguard.events.client import EventClient
from actguard.integrations.manager import IntegrationBootstrap
from actguard.transport.budget_api import BudgetTransport


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
        timeout_s: Optional[float] = None,
        max_retries: Optional[int] = None,
        budget_timeout_s: Optional[float] = None,
        budget_max_retries: Optional[int] = None,
        event_timeout_s: Optional[float] = None,
        event_max_retries: Optional[int] = None,
        backoff_base_ms: int = 200,
        backoff_max_ms: int = 10_000,
    ) -> None:
        resolved_budget_timeout_s = (
            budget_timeout_s if budget_timeout_s is not None else 3.0
        )
        resolved_event_timeout_s = (
            event_timeout_s if event_timeout_s is not None else 5.0
        )
        resolved_budget_max_retries = (
            budget_max_retries if budget_max_retries is not None else 1
        )
        resolved_event_max_retries = (
            event_max_retries if event_max_retries is not None else 8
        )

        if timeout_s is not None:
            if budget_timeout_s is None:
                resolved_budget_timeout_s = timeout_s
            if event_timeout_s is None:
                resolved_event_timeout_s = timeout_s
        if max_retries is not None:
            if budget_max_retries is None:
                resolved_budget_max_retries = max_retries
            if event_max_retries is None:
                resolved_event_max_retries = max_retries

        self.api_key = api_key
        self.gateway_url = gateway_url
        self.event_mode = event_mode
        self.flush_interval_ms = flush_interval_ms
        self.max_batch_events = max_batch_events
        self.max_batch_bytes = max_batch_bytes
        self.max_queue_events = max_queue_events
        self.timeout_s = (
            timeout_s if timeout_s is not None else resolved_event_timeout_s
        )
        self.max_retries = (
            max_retries if max_retries is not None else resolved_event_max_retries
        )
        self.budget_timeout_s = resolved_budget_timeout_s
        self.budget_max_retries = resolved_budget_max_retries
        self.event_timeout_s = resolved_event_timeout_s
        self.event_max_retries = resolved_event_max_retries
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
            budget_timeout_s=resolved_budget_timeout_s,
            budget_max_retries=resolved_budget_max_retries,
            event_timeout_s=resolved_event_timeout_s,
            event_max_retries=resolved_event_max_retries,
            timeout_s=self.timeout_s,
            max_retries=self.max_retries,
            backoff_base_ms=backoff_base_ms,
            backoff_max_ms=backoff_max_ms,
        )
        self._event_client: Optional[EventClient]
        if self._config.events_enabled:
            self._event_client = EventClient(self._config)
        else:
            self._event_client = None
        self._budget_transport = BudgetTransport(self._config)
        self._integration_bootstrap = IntegrationBootstrap()

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
    ) -> ClientRunContext:
        return ClientRunContext(client=self, user_id=user_id, run_id=run_id)

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

    def request_budget_session(
        self,
        *,
        user_id: Optional[str] = None,
        name: Optional[str] = None,
        usd_limit: Optional[float] = None,
        run_id: Optional[str] = None,
        plan_key: Optional[str] = None,
    ):
        from actguard.lazy_budget_session import LazyRequestBudgetSession

        return LazyRequestBudgetSession(
            client=self,
            user_id=user_id,
            name=name,
            usd_limit=usd_limit,
            run_id=run_id,
            plan_key=plan_key,
        )

    def prepare_budget_scope(self) -> None:
        self._integration_bootstrap.ensure_patched()

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
        response = self._budget_transport.post(path="/api/v1/reserve", payload=payload)
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
        self._budget_transport.post(path="/api/v1/settle", payload=payload)

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
        timeout_s = data.get("timeout_s")
        max_retries = data.get("max_retries")
        return cls(
            api_key=data.get("api_key"),
            gateway_url=data.get("gateway_url"),
            event_mode=data.get("event_mode", "verbose"),
            flush_interval_ms=data.get("flush_interval_ms", 1000),
            max_batch_events=data.get("max_batch_events", 100),
            max_batch_bytes=data.get("max_batch_bytes", 256_000),
            max_queue_events=data.get("max_queue_events", 10_000),
            timeout_s=timeout_s,
            max_retries=max_retries,
            budget_timeout_s=data.get("budget_timeout_s"),
            budget_max_retries=data.get("budget_max_retries"),
            event_timeout_s=data.get("event_timeout_s"),
            event_max_retries=data.get("event_max_retries"),
            backoff_base_ms=data.get("backoff_base_ms", 200),
            backoff_max_ms=data.get("backoff_max_ms", 10_000),
        )
