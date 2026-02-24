import threading
from typing import Any, Optional


class InMemoryCache:
    """Thread-safe in-memory cache. Per-process, ephemeral.

    Sufficient for single-process agents. Global enforcement is the gateway's job.
    """

    def __init__(self):
        self._store: dict = {}
        self._lock = threading.RLock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._store.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value

    def transact(self):
        return self._lock


_cache_instance: Optional[InMemoryCache] = None


def get_cache() -> InMemoryCache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = InMemoryCache()
    return _cache_instance
