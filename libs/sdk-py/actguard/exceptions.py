from typing import Literal, Optional


class ToolGuardError(Exception):
    """Base class for all actguard tool guardrail errors."""


class RateLimitExceeded(ToolGuardError):
    def __init__(self, *, func_name, scope_value, max_calls, period, retry_after):
        self.func_name = func_name
        self.scope_value = scope_value
        self.max_calls = max_calls
        self.period = period
        self.retry_after = retry_after  # seconds until retry is safe
        super().__init__(
            f"Rate limit exceeded for '{func_name}' "
            f"(scope={scope_value!r}): {max_calls} calls per {period}s. "
            f"Retry after {retry_after:.1f}s."
        )


class CircuitOpenError(ToolGuardError):
    """Raised when a circuit breaker short-circuits calls for a dependency."""

    def __init__(self, *, dependency_name: str, reset_at: float):
        import time

        self.dependency_name = dependency_name
        self.reset_at = reset_at
        self.retry_after = max(0.0, reset_at - time.time())
        super().__init__(
            f"Circuit open for '{dependency_name}'. "
            f"Retry after {self.retry_after:.1f}s."
        )


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
