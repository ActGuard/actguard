from typing import Literal, Optional


class BudgetExceededError(Exception):
    """Raised when a BudgetGuard limit (token or USD) is exceeded."""

    def __init__(
        self,
        *,
        user_id: str,
        tokens_used: int,
        usd_used: float,
        token_limit: Optional[int],
        usd_limit: Optional[float],
        limit_type: Literal["token", "usd"],
    ) -> None:
        self.user_id = user_id
        self.tokens_used = tokens_used
        self.usd_used = usd_used
        self.token_limit = token_limit
        self.usd_limit = usd_limit
        self.limit_type = limit_type

        if limit_type == "token":
            msg = (
                f"Token limit exceeded for user '{user_id}': "
                f"{tokens_used} / {token_limit} tokens used"
            )
        else:
            msg = (
                f"USD limit exceeded for user '{user_id}': "
                f"${usd_used:.6f} / ${usd_limit:.6f} used"
            )
        super().__init__(msg)
