from __future__ import annotations

from typing import Literal, Optional


class ActGuardViolation(Exception):
    """Base for all policy violations that can be reported via emit_violation()."""

    code: str = ""
    severity: str = "error"
    outcome: str = "blocked"

    def payload(self) -> dict:
        return {}

    def evidence(self) -> list:
        return []


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
    """Raised when run-scoped guards execute without an active runtime context."""

    def __init__(self, message: str = "") -> None:
        default = "No active runtime context. Wrap your agent loop with client.run()."
        super().__init__(message or default)


class NestedRunContextError(ToolGuardError):
    """Raised when client.run() is entered while another run is already active."""

    def __init__(self, message: str = "") -> None:
        default = (
            "Nested runtime contexts are not supported. "
            "Use one active client.run(...) root execution context at a time."
        )
        super().__init__(message or default)


class NestedBudgetGuardError(ToolGuardError):
    """Raised when budget_guard is entered while another budget scope is active."""

    def __init__(self, message: str = "") -> None:
        default = (
            "Nested budget scopes are not supported. "
            "Use one active client.budget_guard(...) per run."
        )
        super().__init__(message or default)


class BudgetConfigurationError(ToolGuardError):
    """Raised when a path tries to redefine the shared root budget configuration."""

    def __init__(self, message: str = "") -> None:
        default = (
            "budget_guard configuration does not match the active root scope. "
            "The first root definition wins for the run."
        )
        super().__init__(message or default)


class BudgetClientMismatchError(ToolGuardError):
    """Raised when budget_guard client does not match active run client."""

    def __init__(self, message: str = "") -> None:
        default = (
            "Active runtime belongs to a different Client. "
            "Use the same client instance for run and budget_guard."
        )
        super().__init__(message or default)


class BudgetTransportError(ActGuardError):
    """Raised when reserve/settle transport calls fail."""


class MaxAttemptsExceeded(ToolGuardError, ActGuardViolation):
    """Raised when a tool exceeds its max_attempts cap within an active run."""

    code = "guard.max_attempts_exceeded"

    def __init__(self, *, run_id: str, tool_name: str, limit: int, used: int) -> None:
        self.run_id = run_id
        self.tool_name = tool_name
        self.limit = limit
        self.used = used
        Exception.__init__(
            self,
            f"MAX_ATTEMPTS_EXCEEDED tool={tool_name!r} used={used}/{limit}"
            f" run={run_id!r}",
        )

    def payload(self) -> dict:
        return {
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "limit": self.limit,
            "used": self.used,
        }


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


class GuardError(ToolGuardError):
    """Raised by @prove / @enforce when a chain-of-custody rule is violated."""

    def __init__(
        self, code: str, message: str, details: dict = None, fix_hint: str = None
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.fix_hint = fix_hint
        super().__init__(message)

    def to_prompt(self) -> str:
        if self.code == "NO_SESSION":
            return (
                "BLOCKED: No active ActGuard session. "
                "Wrap your agent loop with actguard.session()."
            )
        if self.code == "MISSING_FACT":
            kind = self.details.get("kind", "resource")
            value = self.details.get("value", "?")
            hint = self.fix_hint or f"Call a read tool to fetch '{kind}' first."
            return (
                f"BLOCKED: You cannot use {kind}='{value}' because it was not verified "
                f"in this session. Fix: {hint}"
            )
        # TOO_MANY_RESULTS, THRESHOLD_EXCEEDED, PATTERN_BLOCKED
        return f"BLOCKED [{self.code}]: {self.message}. Fix: {self.fix_hint or ''}"


class BudgetExceededError(ActGuardViolation):
    """Raised when a BudgetGuard limit (token or USD) is exceeded."""

    code = "budget.limit_exceeded"

    def __init__(
        self,
        *,
        user_id: Optional[str],
        tokens_used: int,
        usd_used: float,
        usd_limit: Optional[float],
        limit_type: Literal["usd"],
        scope_id: Optional[str] = None,
        scope_name: Optional[str] = None,
        scope_kind: Optional[str] = None,
        parent_scope_id: Optional[str] = None,
        root_scope_id: Optional[str] = None,
    ) -> None:
        self.user_id = user_id
        self.tokens_used = tokens_used
        self.usd_used = usd_used
        self.usd_limit = usd_limit
        self.limit_type = limit_type
        self.scope_id = scope_id
        self.scope_name = scope_name
        self.scope_kind = scope_kind
        self.parent_scope_id = parent_scope_id
        self.root_scope_id = root_scope_id

        user_label = user_id if user_id is not None else "<unknown>"
        limit_label = (
            f"${usd_limit:.6f}" if usd_limit is not None else "<unknown>"
        )
        msg = (
            f"USD limit exceeded for user '{user_label}': "
            f"${usd_used:.6f} / {limit_label} used"
        )
        Exception.__init__(self, msg)

    def payload(self) -> dict:
        return {
            "user_id": self.user_id,
            "tokens_used": self.tokens_used,
            "usd_used": self.usd_used,
            "usd_limit": self.usd_limit,
            "limit_type": self.limit_type,
            "scope_id": self.scope_id,
            "scope_name": self.scope_name,
            "scope_kind": self.scope_kind,
            "parent_scope_id": self.parent_scope_id,
            "root_scope_id": self.root_scope_id,
        }
