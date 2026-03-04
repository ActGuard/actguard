from contextvars import Token
from typing import Optional

from actguard.core.state import BudgetState, reset_state, set_state
from actguard.integrations import patch_all


class BudgetGuard:
    """Context manager that tracks token/USD usage across LLM API calls.

    Usage::

        with BudgetGuard(user_id="alice", usd_limit=0.10) as guard:
            response = openai_client.chat.completions.create(...)
        print(f"Used ${guard.usd_used:.4f}")
    """

    def __init__(
        self,
        *,
        user_id: str,
        token_limit: Optional[int] = None,
        usd_limit: Optional[float] = None,
    ) -> None:
        self.user_id = user_id
        self.token_limit = token_limit
        self.usd_limit = usd_limit
        self._state: Optional[BudgetState] = None
        self._token: Optional[Token] = None
        self.run_id: Optional[str] = None
        self._run_state_token: Optional[Token] = None
        self._owns_run_state: bool = False

    # ------------------------------------------------------------------
    # RunState lifecycle helpers
    # ------------------------------------------------------------------

    def _enter_run_state(self) -> None:
        from actguard.core.run_context import RunState, get_run_state, set_run_state

        existing = get_run_state()
        if existing and existing.run_id:
            self.run_id = existing.run_id
            self._owns_run_state = False
        else:
            import uuid
            self.run_id = "run_" + uuid.uuid4().hex
            self._run_state_token = set_run_state(
                RunState(run_id=self.run_id, user_id=self.user_id or "")
            )
            self._owns_run_state = True

    def _exit_run_state(self) -> None:
        if self._owns_run_state and self._run_state_token is not None:
            from actguard.core.run_context import reset_run_state
            reset_run_state(self._run_state_token)
            self._run_state_token = None
            self._owns_run_state = False

    # ------------------------------------------------------------------
    # Sync context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "BudgetGuard":
        patch_all()
        self._state = BudgetState(
            user_id=self.user_id,
            token_limit=self.token_limit,
            usd_limit=self.usd_limit,
        )
        self._token = set_state(self._state)
        self._enter_run_state()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._exit_run_state()
        if self._token is not None:
            reset_state(self._token)
            self._token = None
        return None  # do not suppress exceptions

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "BudgetGuard":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return self.__exit__(exc_type, exc_val, exc_tb)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def tokens_used(self) -> int:
        if self._state is None:
            return 0
        return self._state.tokens_used

    @property
    def usd_used(self) -> float:
        if self._state is None:
            return 0.0
        return self._state.usd_used
