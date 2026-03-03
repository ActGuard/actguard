from __future__ import annotations

import atexit
import json
import queue
import random
import threading
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from actguard._config import ActGuardConfig
    from actguard.events.envelope import Envelope


class EventClient:
    def __init__(self, config: "ActGuardConfig") -> None:
        self._config = config
        self._queue: queue.Queue = queue.Queue(maxsize=config.max_queue_events)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def enqueue(self, envelope: "Envelope") -> bool:
        try:
            self._queue.put_nowait(envelope)
            return True
        except queue.Full:
            return False

    def flush(self, timeout: float = 5.0) -> None:
        self._queue.join()

    def close(self, wait: bool = True) -> None:
        self._stop.set()
        if wait:
            self._thread.join(timeout=self._config.timeout_s + 1)

    def _worker(self) -> None:
        interval_s = self._config.flush_interval_ms / 1000.0
        max_events = self._config.max_batch_events
        max_bytes = self._config.max_batch_bytes

        while not self._stop.is_set():
            batch = self._drain_batch(interval_s, max_events, max_bytes)
            if batch:
                self._ship_with_retry(batch)

        # Drain remaining items on shutdown
        while True:
            batch = self._drain_batch(0.0, max_events, max_bytes)
            if not batch:
                break
            self._ship_with_retry(batch)

    def _drain_batch(
        self, timeout_s: float, max_events: int, max_bytes: int
    ) -> list:
        batch: list = []
        total_bytes = 0
        deadline = time.monotonic() + timeout_s

        while len(batch) < max_events:
            remaining = deadline - time.monotonic()
            try:
                envelope = self._queue.get(timeout=max(0.0, remaining))
            except queue.Empty:
                break

            serialized = envelope.to_dict()
            item_bytes = len(json.dumps(serialized).encode())

            if batch and total_bytes + item_bytes > max_bytes:
                # Put item back; don't call task_done for it
                try:
                    self._queue.put_nowait(envelope)
                except queue.Full:
                    self._queue.task_done()
                break

            batch.append(serialized)
            total_bytes += item_bytes
            self._queue.task_done()

        return batch

    def _ship_with_retry(self, batch: list) -> None:
        body = json.dumps({"events": batch}).encode()
        url = self._config.gateway_url.rstrip("/") + "/api/v1/events"
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        base_ms = self._config.backoff_base_ms
        max_ms = self._config.backoff_max_ms
        max_retries = self._config.max_retries
        timeout_s = self._config.timeout_s

        for attempt in range(max_retries + 1):
            if self._stop.is_set() and attempt > 0:
                break
            try:
                req = urllib.request.Request(
                    url, data=body, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=timeout_s):
                    return  # success
            except urllib.error.HTTPError as exc:
                status = exc.code
                if status in (400, 401, 403):
                    return  # no retry
                # 429, 5xx → retry
            except Exception:
                pass  # network error → retry

            if attempt < max_retries:
                jitter = random.uniform(0, base_ms)
                delay_ms = min(base_ms * (2**attempt) + jitter, max_ms)
                time.sleep(delay_ms / 1000.0)


# Module-level singleton
_client: Optional[EventClient] = None
_lock = threading.Lock()


def get_client() -> Optional[EventClient]:
    return _client


def reinitialize(config: Optional["ActGuardConfig"]) -> None:
    global _client
    with _lock:
        old = _client
        if old is not None:
            try:
                old.close(wait=False)
            except Exception:
                pass
        if config is not None and config.events_enabled:
            _client = EventClient(config)
        else:
            _client = None


def shutdown() -> None:
    global _client
    with _lock:
        c = _client
        _client = None
    if c is not None:
        try:
            c.close(wait=True)
        except Exception:
            pass


atexit.register(shutdown)
