"""BudgetRecorder protocol and ContextVar for request-scoped budget tracking."""
from __future__ import annotations

from contextvars import ContextVar, Token
from threading import Lock
from typing import Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class BudgetRecorder(Protocol):
    """Interface for recording LLM usage within a budget scope."""

    def record_usage(
        self,
        *,
        provider: str,
        provider_model_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> None: ...

    def check_limits(self) -> None: ...


_current_budget_recorder: ContextVar[Optional[BudgetRecorder]] = ContextVar(
    "_current_budget_recorder", default=None
)

# Fallback registry for threads that don't inherit ContextVar (Python < 3.12 thread pools).
_active_recorders: Dict[int, BudgetRecorder] = {}
_active_recorders_lock: Lock = Lock()


def _in_async_context() -> bool:
    """True when running inside an asyncio event loop."""
    try:
        import asyncio

        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def get_current_budget_recorder() -> Optional[BudgetRecorder]:
    recorder = _current_budget_recorder.get()
    if recorder is not None:
        return recorder
    # Fallback only for worker threads — async tasks have proper ContextVar isolation
    if _in_async_context():
        return None
    with _active_recorders_lock:
        if len(_active_recorders) == 1:
            return next(iter(_active_recorders.values()))
    return None


def set_current_budget_recorder(recorder: BudgetRecorder) -> Token:
    with _active_recorders_lock:
        _active_recorders[id(recorder)] = recorder
    return _current_budget_recorder.set(recorder)


def reset_current_budget_recorder(token: Token) -> None:
    recorder = _current_budget_recorder.get()
    _current_budget_recorder.reset(token)
    if recorder is not None:
        with _active_recorders_lock:
            _active_recorders.pop(id(recorder), None)
