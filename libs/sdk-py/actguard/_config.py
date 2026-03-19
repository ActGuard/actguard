from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class ActGuardConfig:
    gateway_url: Optional[str] = None
    api_key: Optional[str] = None
    debug: bool = False
    event_mode: str = "verbose"  # "off" | "significant" | "verbose"
    flush_interval_ms: int = 1000
    max_batch_events: int = 100
    max_batch_bytes: int = 256_000
    max_queue_events: int = 10_000
    budget_timeout_s: float = 3.0
    budget_max_retries: int = 1
    event_timeout_s: float = 5.0
    event_max_retries: int = 8
    timeout_s: float = 5.0
    max_retries: int = 8
    backoff_base_ms: int = 200
    backoff_max_ms: int = 10_000
    plugins: Optional[List[str]] = None
    plugin_config: Optional[Dict[str, dict]] = None

    @property
    def events_enabled(self) -> bool:
        return bool(self.gateway_url and self.api_key and self.event_mode != "off")
