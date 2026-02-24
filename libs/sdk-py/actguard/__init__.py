"""actguard Python SDK."""

from ._config import configure
from .budget import BudgetGuard
from .exceptions import (
    ActGuardError,
    BudgetExceededError,
    CircuitOpenError,
    DuplicateIdempotencyKey,
    IdempotencyInProgress,
    IdempotencyOutcomeUnknown,
    InvalidIdempotentToolError,
    MaxAttemptsExceeded,
    MissingIdempotencyKeyError,
    MissingRuntimeContextError,
    RateLimitExceeded,
    ToolExecutionError,
    ToolGuardError,
    ToolTimeoutError,
)
from .run_context import RunContext
from .tools import (
    FAIL_ON_DEFAULT,
    FAIL_ON_INFRA_ONLY,
    FAIL_ON_STRICT,
    IGNORE_ON_DEFAULT,
    FailureKind,
    circuit_breaker,
    idempotent,
    max_attempts,
    rate_limit,
    timeout,
    tool,
)
from .tools.timeout import shutdown

__version__ = "0.1.0"

__all__ = [
    "ActGuardError",
    "BudgetGuard",
    "BudgetExceededError",
    "CircuitOpenError",
    "configure",
    "circuit_breaker",
    "DuplicateIdempotencyKey",
    "FAIL_ON_DEFAULT",
    "FAIL_ON_INFRA_ONLY",
    "FAIL_ON_STRICT",
    "FailureKind",
    "idempotent",
    "IdempotencyInProgress",
    "IdempotencyOutcomeUnknown",
    "IGNORE_ON_DEFAULT",
    "InvalidIdempotentToolError",
    "max_attempts",
    "MaxAttemptsExceeded",
    "MissingIdempotencyKeyError",
    "MissingRuntimeContextError",
    "rate_limit",
    "RateLimitExceeded",
    "RunContext",
    "shutdown",
    "timeout",
    "tool",
    "ToolExecutionError",
    "ToolGuardError",
    "ToolTimeoutError",
    "__version__",
]
