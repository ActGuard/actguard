import time
from typing import Dict

_FACTS: Dict[str, float] = {}  # key → expiration timestamp


def mint(session_id: str, scope_hash: str, kind: str, value: str, ttl: float) -> None:
    _FACTS[f"{session_id}:{scope_hash}:{kind}:{value}"] = time.time() + ttl


def exists(session_id: str, scope_hash: str, kind: str, value: str) -> bool:
    exp = _FACTS.get(f"{session_id}:{scope_hash}:{kind}:{value}")
    return exp is not None and exp > time.time()


def prune() -> None:
    now = time.time()
    for k in [k for k, v in _FACTS.items() if v <= now]:
        del _FACTS[k]
