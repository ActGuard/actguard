from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from actguard.client import Client
    from actguard.core.state import BudgetState


@dataclass
class RunState:
    client: Optional["Client"]
    run_id: str
    user_id: Optional[str] = None
    budget_state: Optional["BudgetState"] = None
    budget_reservation: Optional[dict] = None
    _tool_attempts: Dict[str, int] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)
    _idem_store: Dict[Tuple[str, str], Any] = field(default_factory=dict)
    _idem_lock: Lock = field(default_factory=Lock)

    def get_attempt_count(self, tool_id: str) -> int:
        with self._lock:
            return self._tool_attempts.get(tool_id, 0)


_run_state: ContextVar[Optional[RunState]] = ContextVar("_run_state", default=None)


def get_run_state() -> Optional[RunState]:
    return _run_state.get()


def set_run_state(state: RunState) -> Token:
    return _run_state.set(state)


def reset_run_state(token: Token) -> None:
    _run_state.reset(token)


def require_run_state() -> RunState:
    """Return active RunState or raise MissingRuntimeContextError."""
    # Late import to avoid circular dependency between core and exceptions
    from actguard.exceptions import MissingRuntimeContextError

    state = _run_state.get()
    if state is None:
        raise MissingRuntimeContextError(
            "No active runtime context. Wrap your agent loop with client.run()."
        )
    return state
