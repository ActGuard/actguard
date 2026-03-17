"""actguard Python SDK."""

from ._version import __version__
from .budget import BudgetGuard
from .client import Client
from .exceptions import (
    ActGuardError,
    ActGuardPaymentRequired,
    ActGuardToolError,
    MonitoringDegradedError,
)
from .lazy_budget_session import LazyRequestBudgetSession
from .reporting import emit_event, emit_violation
from .session import session
from .tools import (
    FAIL_ON_DEFAULT,
    FAIL_ON_INFRA_ONLY,
    FAIL_ON_STRICT,
    IGNORE_ON_DEFAULT,
    BlockRegex,
    FailureKind,
    RequireFact,
    Threshold,
    circuit_breaker,
    enforce,
    idempotent,
    max_attempts,
    prove,
    rate_limit,
    timeout,
    tool,
)
from .tools.timeout import shutdown

__all__ = [
    "ActGuardError",
    "ActGuardPaymentRequired",
    "ActGuardToolError",
    "MonitoringDegradedError",
    "BlockRegex",
    "BudgetGuard",
    "Client",
    "LazyRequestBudgetSession",
    "circuit_breaker",
    "emit_event",
    "emit_violation",
    "enforce",
    "FAIL_ON_DEFAULT",
    "FAIL_ON_INFRA_ONLY",
    "FAIL_ON_STRICT",
    "FailureKind",
    "idempotent",
    "IGNORE_ON_DEFAULT",
    "max_attempts",
    "prove",
    "rate_limit",
    "RequireFact",
    "session",
    "shutdown",
    "Threshold",
    "timeout",
    "tool",
    "__version__",
]
