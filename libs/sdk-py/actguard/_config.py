import base64
import json
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ActGuardConfig:
    agent_id: str
    gateway_url: Optional[str] = None
    api_key: Optional[str] = None


_config: Optional[ActGuardConfig] = None


def configure(config: Optional[str] = None) -> None:
    """Load agent config from a JSON file path, base64 string, or env var."""
    global _config
    raw = config or os.environ.get("ACTGUARD_CONFIG")
    if raw is None:
        _config = None
        return
    # Try base64 first, fall back to file path
    try:
        data = json.loads(base64.b64decode(raw).decode())
    except Exception:
        with open(raw) as f:
            data = json.load(f)
    _config = ActGuardConfig(**data)


def get_config() -> Optional[ActGuardConfig]:
    return _config
