import uuid
from contextvars import Token
from typing import Optional

from actguard.core.run_context import RunState, reset_run_state, set_run_state


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
