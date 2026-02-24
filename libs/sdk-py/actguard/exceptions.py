from typing import Literal, Optional


class ActGuardError(Exception):
    """Root base class for all ActGuard errors."""


class ToolExecutionError(ActGuardError):
    """Tool ran (or tried to run) but failed. Usually retryable."""


class ToolGuardError(ActGuardError):
    """Base class for all actguard tool guardrail errors.

    Guard blocked execution. Usually non-retryable immediately.
    """


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


class MissingRuntimeContextError(ToolGuardError):
    """Raised when @max_attempts is called without an active RunContext."""

    def __init__(self, message: str = "") -> None:
        default = "No active RunContext. Wrap your agent loop with RunContext()."
        super().__init__(message or default)


class MaxAttemptsExceeded(ToolGuardError):
    """Raised when a tool exceeds its max_attempts cap within a RunContext."""

    def __init__(self, *, run_id: str, tool_name: str, limit: int, used: int) -> None:
        self.run_id = run_id
        self.tool_name = tool_name
        self.limit = limit
        self.used = used
        super().__init__(
            f"MAX_ATTEMPTS_EXCEEDED tool={tool_name!r} used={used}/{limit}"
            f" run={run_id!r}"
        )


class ToolTimeoutError(ToolExecutionError):
    """Raised when a tool exceeds its wall-clock time limit."""

    def __init__(self, tool_name: str, timeout_s: float, run_id: str | None = None):
        super().__init__(f"TOOL_TIMEOUT tool='{tool_name}' limit={timeout_s}s")
        self.tool_name = tool_name
        self.timeout_s = timeout_s
        self.run_id = run_id


class InvalidIdempotentToolError(ActGuardError):
    """Raised at decoration time if the function lacks an 'idempotency_key' param."""


class MissingIdempotencyKeyError(ToolGuardError):
    """Raised when the caller passes None or empty string as idempotency_key."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(
            f"idempotency_key must be a non-empty string for tool '{tool_name}'."
        )


class IdempotencyInProgress(ToolGuardError):
    """Raised when another thread/task is currently executing this key."""

    def __init__(self, tool_name: str, key: str) -> None:
        self.tool_name = tool_name
        self.key = key
        super().__init__(f"Tool '{tool_name}' with key={key!r} is already in progress.")


class DuplicateIdempotencyKey(ToolGuardError):
    """Raised when execution is DONE and on_duplicate='raise'."""

    def __init__(self, tool_name: str, key: str) -> None:
        self.tool_name = tool_name
        self.key = key
        super().__init__(
            f"Tool '{tool_name}' with key={key!r} has already been executed."
        )


class IdempotencyOutcomeUnknown(ToolGuardError):
    """Raised when a previous attempt failed unsafely; retry blocked until TTL."""

    def __init__(self, tool_name: str, key: str, last_error_type: type) -> None:
        self.tool_name = tool_name
        self.key = key
        self.last_error_type = last_error_type
        super().__init__(
            f"Tool '{tool_name}' with key={key!r} has an unknown outcome "
            f"after {last_error_type.__name__}. Retry blocked until TTL expires."
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
