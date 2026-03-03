import base64
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ActGuardConfig:
    agent_id: str = ""
    gateway_url: Optional[str] = None
    api_key: Optional[str] = None
    event_mode: str = "verbose"  # "off" | "significant" | "verbose"
    flush_interval_ms: int = 1000
    max_batch_events: int = 100
    max_batch_bytes: int = 256_000
    max_queue_events: int = 10_000
    timeout_s: float = 5.0
    max_retries: int = 8
    backoff_base_ms: int = 200
    backoff_max_ms: int = 10_000
    plugins: Optional[List[str]] = None
    plugin_config: Optional[Dict[str, dict]] = None

    @property
    def events_enabled(self) -> bool:
        return bool(self.gateway_url and self.api_key and self.event_mode != "off")


_config: Optional[ActGuardConfig] = None


def configure(
    config: Optional[str] = None,
    *,
    agent_id: str = "",
    gateway_url: Optional[str] = None,
    api_key: Optional[str] = None,
    event_mode: str = "verbose",
    flush_interval_ms: int = 1000,
    max_batch_events: int = 100,
    max_batch_bytes: int = 256_000,
    max_queue_events: int = 10_000,
    timeout_s: float = 5.0,
    max_retries: int = 8,
    backoff_base_ms: int = 200,
    backoff_max_ms: int = 10_000,
    plugins: Optional[List[str]] = None,
    plugin_config: Optional[Dict[str, dict]] = None,
) -> None:
    """Load agent config from kwargs, a JSON file path, base64 string, or env var."""
    global _config

    if gateway_url is not None or api_key is not None:
        # Direct kwargs path
        _config = ActGuardConfig(
            agent_id=agent_id,
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
            plugins=plugins,
            plugin_config=plugin_config,
        )
    else:
        # Original file/env path
        raw = config or os.environ.get("ACTGUARD_CONFIG")
        if raw is None:
            _config = None
        else:
            try:
                data = json.loads(base64.b64decode(raw).decode())
            except Exception:
                with open(raw) as f:
                    data = json.load(f)
            _config = ActGuardConfig(**data)

    try:
        from actguard.events.client import reinitialize as _reinitialize_event_client

        _reinitialize_event_client(_config)
    except Exception:
        pass

    try:
        from actguard.plugins import _load as _load_plugins

        _load_plugins(_config)
    except Exception:
        pass


def get_config() -> Optional[ActGuardConfig]:
    return _config
