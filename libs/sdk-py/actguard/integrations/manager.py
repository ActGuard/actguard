from __future__ import annotations

from threading import Lock


class IntegrationBootstrap:
    """Patch supported provider SDKs once per process."""

    def __init__(self) -> None:
        self._lock = Lock()

    def ensure_patched(self) -> None:
        with self._lock:
            from .anthropic import patch_anthropic
            from .google import patch_google
            from .openai import patch_openai

            patch_openai()
            patch_anthropic()
            patch_google()


_bootstrap = IntegrationBootstrap()


def ensure_patched() -> None:
    _bootstrap.ensure_patched()


def patch_all() -> None:
    ensure_patched()
