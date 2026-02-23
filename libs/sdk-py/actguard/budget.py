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
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
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
