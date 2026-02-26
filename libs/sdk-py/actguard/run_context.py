import uuid
from contextvars import Token
from typing import Dict, Optional, Tuple

from actguard.core.run_context import RunState, reset_run_state, set_run_state
from actguard.tools._scope import reset_session, set_session


class RunContext:
    def __init__(self, *, run_id: Optional[str] = None) -> None:
        self.run_id: str = run_id if run_id is not None else str(uuid.uuid4())
        self._state: Optional[RunState] = None
        self._token: Optional[Token] = None

    def __enter__(self) -> "RunContext":
        self._state = RunState(run_id=self.run_id)
        self._token = set_run_state(self._state)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._token is not None:
            reset_run_state(self._token)
            self._token = None

    async def __aenter__(self) -> "RunContext":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return self.__exit__(exc_type, exc_val, exc_tb)

    def get_attempt_count(self, tool_id: str) -> int:
        if self._state is None:
            return 0
        return self._state.get_attempt_count(tool_id)


class GuardSession:
    """Context manager that activates a Chain-of-Custody session.

    Usage::

        with actguard.session("run-123", {"user_id": "u42"}):
            result = list_orders(user_id="u42")
            delete_order(order_id="o1")
    """

    def __init__(self, id: str, scope: Dict[str, str] = None) -> None:
        self.id = id
        self.scope = scope or {}
        for k, v in self.scope.items():
            if not isinstance(v, str):
                raise TypeError(
                    f"Scope values must be strings, got {type(v)} for key {k!r}"
                )
        self._tokens: Optional[Tuple] = None

    def __enter__(self) -> "GuardSession":
        self._tokens = set_session(self.id, self.scope)
        return self

    def __exit__(self, *_) -> None:
        reset_session(self._tokens)
        self._tokens = None

    async def __aenter__(self) -> "GuardSession":
        return self.__enter__()

    async def __aexit__(self, *a) -> None:
        return self.__exit__(*a)


def session(id: str, scope: Dict[str, str] = None) -> GuardSession:
    """Factory for a GuardSession context manager.

    Args:
        id: Unique session identifier (e.g. run ID, request ID).
        scope: Optional dict of string key/value pairs that scope fact visibility
               (e.g. ``{"user_id": "u42"}``).
    """
    return GuardSession(id=id, scope=scope)
