SIGNIFICANT: frozenset = frozenset({
    "budget.blocked",
    "budget.limit_exceeded",
    "guard.blocked",
    "guard.intervention",
    "guard.max_attempts_exceeded",
    "policy.blocked",
    "run.blocked",
    "run.failed",
    "tool.failure",
})

VERBOSE: frozenset = SIGNIFICANT | frozenset({
    "budget.check",
    "budget.consumed",
    "run.completed",
    "run.started",
    "tool.invoked",
    "tool.succeeded",
})
