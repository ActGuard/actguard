from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from actguard._config import ActGuardConfig
from actguard._debug import ensure_actguard_debug_handler
from actguard.costs import CuTariff
from actguard.core.runtime import ClientRunContext
from actguard.events.client import EventClient
from actguard.integrations.manager import IntegrationBootstrap
from actguard.transport.budget_api import BudgetTransport

DEFAULT_GATEWAY_URL = "https://api.actguard.ai"


class Client:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        gateway_url: Optional[str] = None,
        debug: bool = False,
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

        resolved_gateway_url = (
            gateway_url if gateway_url is not None else DEFAULT_GATEWAY_URL
        )

        self.api_key = api_key
        self.gateway_url = resolved_gateway_url
        self.debug = debug
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

        if debug:
            ensure_actguard_debug_handler()

        self._config = ActGuardConfig(
            gateway_url=resolved_gateway_url,
            api_key=api_key,
            debug=debug,
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
        self._cu_tariff_cache: Optional[CuTariff] = None

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
        cost_limit: Optional[int] = None,
        run_id: Optional[str] = None,
        plan_key: Optional[str] = None,
    ):
        """Create a budget scope inside an active ``client.run(...)`` block.

        Use this when you want to attribute model/tool usage to a specific step
        of a run, or when you want ActGuard to enforce a maximum budget for that
        scope.

        ``cost_limit`` is a simple integer cap in cost units (CU), where
        ``1_000 CU`` is roughly equal to ``$1.00``. When usage recorded in the
        scope exceeds that cap, ActGuard raises a budget error. Leave it unset
        if you only want attribution without enforcing a limit.

        Nested scopes are useful for labeling sub-steps such as ``search`` or
        ``rerank``. Child scopes should usually use a smaller ``cost_limit`` than
        their parent scope.

        Example:
            >>> client = actguard.Client.from_env()
            >>> with client.run(run_id="req-42"):
            ...     with client.budget_guard(name="search", cost_limit=1_000):
            ...         call_model()
        """
        from actguard.budget import BudgetGuard

        return BudgetGuard(
            client=self,
            user_id=user_id,
            name=name,
            cost_limit=cost_limit,
            run_id=run_id,
            plan_key=plan_key,
        )

    def prepare_budget_scope(self) -> None:
        self._integration_bootstrap.ensure_patched()

    def get_cu_tariff(self, *, force_refresh: bool = False) -> CuTariff:
        if self._cu_tariff_cache is not None and not force_refresh:
            return self._cu_tariff_cache

        payload = self._budget_transport.get_public(path="/api/v1/cu-tariff")
        tariff = CuTariff.from_payload(payload)
        self._cu_tariff_cache = tariff
        return tariff

    def reserve_budget(
        self,
        *,
        run_id: str,
        cost_limit: Optional[int] = None,
        plan_key: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Mapping[str, Any]:
        from actguard.exceptions import BudgetTransportError

        payload = {
            "run_id": run_id,
        }
        if cost_limit is not None:
            payload["cost_limit"] = cost_limit
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
        if not isinstance(response.get("status"), str):
            return {"status": "reserved", **response}
        return response

    def settle_budget(
        self,
        *,
        reserve_id: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        usage_breakdown: list[Mapping[str, object]],
        cache_write_tokens_5m: Optional[int] = None,
        cache_write_tokens_1h: Optional[int] = None,
        web_search_count: Optional[int] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "reserve_id": reserve_id,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "usage_breakdown": [dict(entry) for entry in usage_breakdown],
        }
        if cache_write_tokens_5m is not None:
            payload["cache_write_tokens_5m"] = cache_write_tokens_5m
        if cache_write_tokens_1h is not None:
            payload["cache_write_tokens_1h"] = cache_write_tokens_1h
        if web_search_count is not None:
            payload["web_search_count"] = web_search_count
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        return self._budget_transport.post(path="/api/v1/settle", payload=payload)

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
            debug=bool(data.get("debug", False)),
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
