import uuid
from contextvars import Token
from typing import Dict, Optional, Tuple

from actguard.core.run_context import RunState, reset_run_state, set_run_state
from actguard.tools._scope import reset_session, set_session


class RunContext:
    def __init__(
        self, *, run_id: Optional[str] = None, user_id: Optional[str] = None
    ) -> None:
        self.run_id: str = run_id if run_id is not None else str(uuid.uuid4())
        self.user_id: str = user_id or ""
        self._state: Optional[RunState] = None
        self._token: Optional[Token] = None

    def __enter__(self) -> "RunContext":
        self._state = RunState(run_id=self.run_id, user_id=self.user_id)
        self._token = set_run_state(self._state)
        try:
            from actguard.reporting import emit_event

            emit_event(
                "run",
                "started",
                {"run_id": self.run_id, "user_id": self.user_id},
            )
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            from actguard.reporting import emit_event

            if exc_type is None:
                emit_event(
                    "run",
                    "completed",
                    {"run_id": self.run_id},
                    outcome="completed",
                )
            else:
                from actguard.exceptions import ActGuardViolation

                if issubclass(exc_type, ActGuardViolation):
                    emit_event(
                        "run",
                        "blocked",
                        {"run_id": self.run_id, "error_type": exc_type.__name__},
                        severity="error",
                        outcome="blocked",
                    )
                else:
                    emit_event(
                        "run",
                        "failed",
                        {"run_id": self.run_id, "error_type": exc_type.__name__},
                        severity="error",
                        outcome="failed",
                    )
        except Exception:
            pass
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
