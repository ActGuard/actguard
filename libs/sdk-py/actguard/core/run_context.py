from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from actguard.client import Client
    from actguard.core.budget_context import SharedBudgetState


@dataclass
class RunState:
    client: Optional["Client"]
    run_id: str
    user_id: Optional[str] = None
    _tool_attempts: Dict[str, int] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)
    _idem_store: Dict[Tuple[str, str], Any] = field(default_factory=dict)
    _idem_lock: Lock = field(default_factory=Lock)
    _budget_root: Optional["SharedBudgetState"] = None
    _budget_lock: Lock = field(default_factory=Lock)

    def get_attempt_count(self, tool_id: str) -> int:
        with self._lock:
            return self._tool_attempts.get(tool_id, 0)

    def get_budget_root(self) -> Optional["SharedBudgetState"]:
        with self._budget_lock:
            return self._budget_root

    def set_budget_root(self, root: Optional["SharedBudgetState"]) -> None:
        with self._budget_lock:
            self._budget_root = root

    def acquire_budget_root(
        self,
        *,
        user_id: Optional[str],
        scope_name: Optional[str],
        usd_limit: Optional[float],
        usd_limit_micros: Optional[int],
        plan_key: Optional[str],
    ) -> tuple["SharedBudgetState", bool]:
        from actguard.core.budget_context import SharedBudgetState

        with self._budget_lock:
            created = False
            if self._budget_root is None:
                self._budget_root = SharedBudgetState(
                    user_id=user_id,
                    run_id=self.run_id,
                    root_scope_name=scope_name,
                    root_budget_limit=usd_limit,
                    root_budget_limit_micros=usd_limit_micros,
                    plan_key=plan_key,
                )
                created = True
            root = self._budget_root
            root.attach()
        return root, created

    def release_budget_root(self, root: "SharedBudgetState") -> bool:
        with self._budget_lock:
            should_settle = root.detach()
            if should_settle and self._budget_root is root:
                self._budget_root = None
            return should_settle


_run_state: ContextVar[Optional[RunState]] = ContextVar("_run_state", default=None)

# Fallback registry for threads that don't inherit ContextVar (Python < 3.12 thread pools).
# Keyed by id() since RunState is not hashable (contains Lock/dict fields).
_active_states: Dict[int, RunState] = {}
_active_states_lock: Lock = Lock()


def _in_async_context() -> bool:
    """True when running inside an asyncio event loop."""
    try:
        import asyncio
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def get_run_state() -> Optional[RunState]:
    state = _run_state.get()
    if state is not None:
        return state
    # Fallback only for worker threads — async tasks have proper ContextVar isolation
    if _in_async_context():
        return None
    with _active_states_lock:
        if len(_active_states) == 1:
            return next(iter(_active_states.values()))
    return None


def set_run_state(state: RunState) -> Token:
    with _active_states_lock:
        _active_states[id(state)] = state
    return _run_state.set(state)


def reset_run_state(token: Token) -> None:
    state = _run_state.get()
    _run_state.reset(token)
    if state is not None:
        with _active_states_lock:
            _active_states.pop(id(state), None)


def require_run_state() -> RunState:
    """Return active RunState or raise MissingRuntimeContextError."""
    # Late import to avoid circular dependency between core and exceptions
    from actguard.exceptions import MissingRuntimeContextError

    state = get_run_state()
    if state is None:
        raise MissingRuntimeContextError(
            "No active runtime context. Wrap your agent loop with client.run()."
        )
    return state
