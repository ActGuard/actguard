from __future__ import annotations

from typing import Any, Literal, Optional


class ActGuardError(Exception):
    """Root base class for all ActGuard SDK errors."""

    code: str = ""
    reason: str = ""
    retryable: bool | None = None
    event_category: str = ""
    event_name: str = ""
    severity: str = ""
    outcome: str = ""

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
        retryable: bool | None = None,
        status_code: int | None = None,
        event_category: str | None = None,
        event_name: str | None = None,
        severity: str | None = None,
        outcome: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = self.code if code is None else code
        self.reason = self.reason if reason is None else reason
        self.details = {} if details is None else dict(details)
        self.cause = cause
        if retryable is not None:
            self.retryable = retryable
        self.status_code = status_code
        if event_category is not None:
            self.event_category = event_category
        if event_name is not None:
            self.event_name = event_name
        if severity is not None:
            self.severity = severity
        if outcome is not None:
            self.outcome = outcome

    @property
    def is_reportable(self) -> bool:
        return bool(self.event_category and self.event_name)

    def payload(self) -> dict[str, Any]:
        return dict(self.details)

    def evidence(self) -> list[Any]:
        return []


class ActGuardToolError(ActGuardError):
    """Public catch-all for failures on protected tool/action paths."""


class ActGuardRuntimeError(ActGuardError):
    """Internal runtime/state failure not intended as a broad tool-path catch."""


class ActGuardRuntimeContextError(ActGuardRuntimeError):
    """Active runtime state/context is missing or incompatible."""


class ActGuardUsageError(ActGuardError):
    """Caller or SDK configuration/usage is invalid."""


class ToolExecutionError(ActGuardToolError):
    """Protected tool ran (or tried to run) but failed."""

    event_category = "tool"
    event_name = "failure"
    severity = "error"
    outcome = "failed"


class ToolGuardError(ActGuardToolError):
    """Compatibility/internal base for blocked protected-tool guard outcomes."""

    event_category = "guard"
    event_name = "blocked"
    severity = "error"
    outcome = "blocked"


class ActGuardPaymentRequired(ActGuardRuntimeError):
    """Public catch for reserve/settle payment-required conditions."""

    code = "budget.payment_required"
    reason = "payment_required"

    def __init__(self, *, path: str, status: int = 402, cause: BaseException | None = None) -> None:
        self.path = path
        self.status = status
        super().__init__(
            f"Budget API request failed with status {status} at {path}: payment required.",
            code=self.code,
            reason=self.reason,
            details={"path": path, "status": status},
            cause=cause,
            retryable=False,
            status_code=status,
        )


class RateLimitExceeded(ToolGuardError):
    code = "guard.rate_limit_exceeded"
    reason = "rate_limit_exceeded"

    def __init__(self, *, func_name: str, scope_value: str, max_calls: int, period: float, retry_after: float):
        self.func_name = func_name
        self.tool_name = func_name
        self.scope_value = scope_value
        self.max_calls = max_calls
        self.period = period
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit exceeded for '{func_name}' (scope={scope_value!r}): "
            f"{max_calls} calls per {period}s. Retry after {retry_after:.1f}s.",
            code=self.code,
            reason=self.reason,
            details={
                "tool_name": func_name,
                "scope_value": scope_value,
                "max_calls": max_calls,
                "period": period,
                "retry_after": retry_after,
            },
            retryable=True,
        )


class CircuitOpenError(ToolGuardError):
    """Raised when a circuit breaker short-circuits calls for a dependency."""

    code = "guard.circuit_open"
    reason = "circuit_open"

    def __init__(self, *, dependency_name: str, reset_at: float):
        import time

        self.dependency_name = dependency_name
        self.tool_name = dependency_name
        self.reset_at = reset_at
        self.retry_after = max(0.0, reset_at - time.time())
        super().__init__(
            f"Circuit open for '{dependency_name}'. Retry after {self.retry_after:.1f}s.",
            code=self.code,
            reason=self.reason,
            details={
                "dependency_name": dependency_name,
                "reset_at": reset_at,
                "retry_after": self.retry_after,
            },
            retryable=True,
        )


class MissingRuntimeContextError(ActGuardRuntimeContextError):
    """Raised when runtime-scoped APIs execute without an active runtime context."""

    code = "runtime.context_missing"
    reason = "missing_runtime_context"

    def __init__(self, message: str = "") -> None:
        default = "No active runtime context. Wrap your agent loop with client.run()."
        super().__init__(
            message or default,
            code=self.code,
            reason=self.reason,
            details={"requires": "client.run"},
            retryable=False,
        )


class NestedRuntimeContextError(ActGuardRuntimeContextError):
    """Raised when client.run() is entered while another run is already active."""

    code = "runtime.context_nested"
    reason = "nested_runtime_context"

    def __init__(self, message: str = "") -> None:
        default = (
            "Nested runtime contexts are not supported. "
            "Use one active client.run(...) root execution context at a time."
        )
        super().__init__(
            message or default,
            code=self.code,
            reason=self.reason,
            retryable=False,
        )


class NestedBudgetGuardError(ActGuardRuntimeContextError):
    """Raised when budget_guard is entered while another budget scope is active."""

    code = "runtime.budget_nested"
    reason = "nested_budget_guard"

    def __init__(self, message: str = "") -> None:
        default = (
            "Nested budget scopes are not supported. "
            "Use one active client.budget_guard(...) per run."
        )
        super().__init__(
            message or default,
            code=self.code,
            reason=self.reason,
            retryable=False,
        )


class BudgetConfigurationError(ActGuardRuntimeContextError):
    """Raised when a path tries to redefine the shared root budget configuration."""

    code = "runtime.budget_configuration_mismatch"
    reason = "budget_configuration_mismatch"

    def __init__(self, message: str = "") -> None:
        default = (
            "budget_guard configuration does not match the active root scope. "
            "The first root definition wins for the run."
        )
        super().__init__(
            message or default,
            code=self.code,
            reason=self.reason,
            retryable=False,
        )


class BudgetClientMismatchError(ActGuardRuntimeContextError):
    """Raised when budget_guard client does not match active run client."""

    code = "runtime.client_mismatch"
    reason = "budget_client_mismatch"

    def __init__(self, message: str = "") -> None:
        default = (
            "Active runtime belongs to a different Client. "
            "Use the same client instance for run and budget_guard."
        )
        super().__init__(
            message or default,
            code=self.code,
            reason=self.reason,
            retryable=False,
        )


class BudgetTransportError(ActGuardRuntimeError):
    """Raised when reserve/settle transport calls fail."""

    code = "runtime.transport_error"
    reason = "budget_transport_error"

    def __init__(self, message: str = "", *, cause: BaseException | None = None, status_code: int | None = None):
        super().__init__(
            message,
            code=self.code,
            reason=self.reason,
            cause=cause,
            retryable=True,
            status_code=status_code,
        )


class MaxAttemptsExceeded(ToolGuardError):
    """Raised when a tool exceeds its max_attempts cap within an active run."""

    code = "guard.max_attempts_exceeded"
    reason = "max_attempts_exceeded"

    def __init__(self, *, run_id: str, tool_name: str, limit: int, used: int) -> None:
        self.run_id = run_id
        self.tool_name = tool_name
        self.limit = limit
        self.used = used
        super().__init__(
            f"MAX_ATTEMPTS_EXCEEDED tool={tool_name!r} used={used}/{limit} run={run_id!r}",
            code=self.code,
            reason=self.reason,
            details={
                "run_id": run_id,
                "tool_name": tool_name,
                "limit": limit,
                "used": used,
            },
            retryable=False,
        )


class ToolTimeoutError(ToolExecutionError):
    """Raised when a tool exceeds its wall-clock time limit."""

    code = "tool.timeout"
    reason = "timeout"

    def __init__(self, tool_name: str, timeout_s: float, run_id: str | None = None):
        self.tool_name = tool_name
        self.timeout_s = timeout_s
        self.run_id = run_id
        super().__init__(
            f"TOOL_TIMEOUT tool='{tool_name}' limit={timeout_s}s",
            code=self.code,
            reason=self.reason,
            details={
                "tool_name": tool_name,
                "timeout_s": timeout_s,
                "run_id": run_id,
            },
            retryable=True,
        )


class InvalidIdempotentToolError(ActGuardUsageError):
    """Raised at decoration time if the function lacks an 'idempotency_key' param."""

    code = "usage.invalid_idempotent_tool"
    reason = "invalid_idempotent_tool"

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code=self.code,
            reason=self.reason,
            retryable=False,
        )


class MissingIdempotencyKeyError(ActGuardUsageError):
    """Raised when the caller passes None or empty string as idempotency_key."""

    code = "usage.missing_idempotency_key"
    reason = "missing_idempotency_key"

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(
            f"idempotency_key must be a non-empty string for tool '{tool_name}'.",
            code=self.code,
            reason=self.reason,
            details={"tool_name": tool_name},
            retryable=False,
        )


class IdempotencyInProgress(ToolGuardError):
    """Raised when another thread/task is currently executing this key."""

    code = "guard.idempotency_in_progress"
    reason = "idempotency_in_progress"

    def __init__(self, tool_name: str, key: str) -> None:
        self.tool_name = tool_name
        self.key = key
        super().__init__(
            f"Tool '{tool_name}' with key={key!r} is already in progress.",
            code=self.code,
            reason=self.reason,
            details={"tool_name": tool_name, "key": key},
            retryable=True,
        )


class DuplicateIdempotencyKey(ToolGuardError):
    """Raised when execution is DONE and on_duplicate='raise'."""

    code = "guard.idempotency_duplicate"
    reason = "idempotency_duplicate"

    def __init__(self, tool_name: str, key: str) -> None:
        self.tool_name = tool_name
        self.key = key
        super().__init__(
            f"Tool '{tool_name}' with key={key!r} has already been executed.",
            code=self.code,
            reason=self.reason,
            details={"tool_name": tool_name, "key": key},
            retryable=False,
        )


class IdempotencyOutcomeUnknown(ToolGuardError):
    """Raised when a previous attempt failed unsafely; retry blocked until TTL."""

    code = "guard.idempotency_outcome_unknown"
    reason = "idempotency_outcome_unknown"

    def __init__(self, tool_name: str, key: str, last_error_type: type) -> None:
        self.tool_name = tool_name
        self.key = key
        self.last_error_type = last_error_type
        super().__init__(
            f"Tool '{tool_name}' with key={key!r} has an unknown outcome "
            f"after {last_error_type.__name__}. Retry blocked until TTL expires.",
            code=self.code,
            reason=self.reason,
            details={
                "tool_name": tool_name,
                "key": key,
                "last_error_type": last_error_type.__name__,
            },
            retryable=False,
        )


class PolicyViolationError(ToolGuardError):
    """Raised by @prove / @enforce when a chain-of-custody rule is violated."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        fix_hint: str | None = None,
    ) -> None:
        self.fix_hint = fix_hint
        merged_details = {} if details is None else dict(details)
        if fix_hint is not None:
            merged_details.setdefault("fix_hint", fix_hint)
        super().__init__(
            message,
            code=code,
            reason=code.lower(),
            details=merged_details,
            retryable=False,
        )

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
        return f"BLOCKED [{self.code}]: {self.message}. Fix: {self.fix_hint or ''}"


class BudgetExceededError(ToolGuardError):
    """Raised when a BudgetGuard limit is exceeded during protected execution."""

    code = "budget.limit_exceeded"
    reason = "budget_exhausted"
    event_category = "budget"
    event_name = "limit_exceeded"
    severity = "error"
    outcome = "blocked"

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
        limit_label = f"${usd_limit:.6f}" if usd_limit is not None else "<unknown>"
        super().__init__(
            f"USD limit exceeded for user '{user_label}': ${usd_used:.6f} / {limit_label} used",
            code=self.code,
            reason=self.reason,
            details={
                "user_id": user_id,
                "tokens_used": tokens_used,
                "usd_used": usd_used,
                "usd_limit": usd_limit,
                "limit_type": limit_type,
                "scope_id": scope_id,
                "scope_name": scope_name,
                "scope_kind": scope_kind,
                "parent_scope_id": parent_scope_id,
                "root_scope_id": root_scope_id,
            },
            retryable=False,
            event_category=self.event_category,
            event_name=self.event_name,
            severity=self.severity,
            outcome=self.outcome,
        )

    def payload(self) -> dict[str, Any]:
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


class CircuitBreakerConfigurationError(ActGuardUsageError):
    code = "usage.circuit_breaker_configuration"
    reason = "circuit_breaker_configuration"

    def __init__(self, message: str) -> None:
        super().__init__(message, code=self.code, reason=self.reason, retryable=False)


class MaxAttemptsConfigurationError(ActGuardUsageError):
    code = "usage.max_attempts_configuration"
    reason = "max_attempts_configuration"

    def __init__(self, message: str) -> None:
        super().__init__(message, code=self.code, reason=self.reason, retryable=False)


class TimeoutUsageError(ActGuardUsageError):
    code = "usage.timeout_configuration"
    reason = "timeout_configuration"

    def __init__(self, message: str) -> None:
        super().__init__(message, code=self.code, reason=self.reason, retryable=False)


class ScopeValidationError(ActGuardUsageError):
    code = "usage.scope_validation"
    reason = "scope_validation"

    def __init__(self, message: str) -> None:
        super().__init__(message, code=self.code, reason=self.reason, retryable=False)


class SessionUsageError(ActGuardUsageError):
    code = "usage.session_configuration"
    reason = "session_configuration"

    def __init__(self, message: str) -> None:
        super().__init__(message, code=self.code, reason=self.reason, retryable=False)


class ReportingContractError(ActGuardUsageError):
    code = "usage.reporting_contract"
    reason = "reporting_contract"

    def __init__(self, message: str) -> None:
        super().__init__(message, code=self.code, reason=self.reason, retryable=False)


BudgetPaymentRequiredError = ActGuardPaymentRequired
NestedRunContextError = NestedRuntimeContextError
GuardError = PolicyViolationError
ActGuardViolation = ToolGuardError
