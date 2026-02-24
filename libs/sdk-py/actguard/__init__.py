"""actguard Python SDK."""
from ._config import configure
from .budget import BudgetGuard
from .exceptions import (
    BudgetExceededError,
    CircuitOpenError,
    RateLimitExceeded,
    ToolGuardError,
)
from .tools import (
    FAIL_ON_DEFAULT,
    FAIL_ON_INFRA_ONLY,
    FAIL_ON_STRICT,
    IGNORE_ON_DEFAULT,
    FailureKind,
    circuit_breaker,
    rate_limit,
    tool,
)

__version__ = "0.1.0"

__all__ = [
    "BudgetGuard",
    "BudgetExceededError",
    "CircuitOpenError",
    "configure",
    "circuit_breaker",
    "FAIL_ON_DEFAULT",
    "FAIL_ON_INFRA_ONLY",
    "FAIL_ON_STRICT",
    "FailureKind",
    "IGNORE_ON_DEFAULT",
    "rate_limit",
    "RateLimitExceeded",
    "tool",
    "ToolGuardError",
    "__version__",
]
