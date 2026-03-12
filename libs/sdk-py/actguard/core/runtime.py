from __future__ import annotations

import uuid
from contextvars import Token
from typing import TYPE_CHECKING, Optional

from actguard._monitoring import warn_monitoring_issue
from actguard.core.run_context import (
    RunState,
    get_run_state,
    reset_run_state,
    set_run_state,
)

if TYPE_CHECKING:
    from actguard.client import Client


class ClientRunContext:
    """Context manager installed by Client.run(...)."""

    def __init__(
        self,
        *,
        client: "Client",
        user_id: Optional[str],
        run_id: Optional[str],
    ) -> None:
        self.client = client
        self.run_id = run_id if run_id is not None else str(uuid.uuid4())
        self.user_id = user_id
        self._state: Optional[RunState] = None
        self._token: Optional[Token] = None

    def __enter__(self) -> "ClientRunContext":
        from actguard.exceptions import NestedRuntimeContextError
        from actguard.reporting import emit_event

        active = get_run_state()
        if active is not None:
            raise NestedRuntimeContextError(
                "Nested runtime contexts are not supported. "
                f"Active run_id={active.run_id!r}; finish the current client.run(...) "
                "before entering another."
            )

        self._state = RunState(
            client=self.client,
            run_id=self.run_id,
            user_id=self.user_id,
        )
        self._token = set_run_state(self._state)
        try:
            emit_event(
                "run",
                "start",
                {
                    "run_id": self._state.run_id,
                    "user_id": self._state.user_id,
                },
            )
        except Exception as exc:
            warn_monitoring_issue(
                subsystem="reporting",
                operation="run.start",
                exc=exc,
                stacklevel=2,
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        from actguard.reporting import emit_event

        run_id = self.run_id
        if self._state is not None:
            run_id = self._state.run_id

        try:
            if exc_type is None:
                emit_event(
                    "run",
                    "end",
                    {"run_id": run_id},
                    outcome="success",
                )
            else:
                from actguard.exceptions import ActGuardError

                if (
                    issubclass(exc_type, ActGuardError)
                    and getattr(exc_type, "outcome", "") == "blocked"
                ):
                    emit_event(
                        "run",
                        "end",
                        {"run_id": run_id, "error_type": exc_type.__name__},
                        severity="error",
                        outcome="blocked",
                    )
                else:
                    emit_event(
                        "run",
                        "end",
                        {"run_id": run_id, "error_type": exc_type.__name__},
                        severity="error",
                        outcome="failed",
                    )
        except Exception as exc:
            warn_monitoring_issue(
                subsystem="reporting",
                operation="run.end",
                exc=exc,
                stacklevel=2,
            )

        if self._token is not None:
            reset_run_state(self._token)
            self._token = None
        self._state = None

    async def __aenter__(self) -> "ClientRunContext":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return self.__exit__(exc_type, exc_val, exc_tb)

    def get_attempt_count(self, tool_id: str) -> int:
        if self._state is None:
            return 0
        return self._state.get_attempt_count(tool_id)
