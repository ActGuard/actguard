SIGNIFICANT: frozenset = frozenset({
    "budget.blocked",
    "budget.limit_exceeded",
    "guard.max_attempts_exceeded",
    "policy.blocked",
    "run.blocked",
    "run.failed",
    "tool.blocked",
    "tool.failed",
})

VERBOSE: frozenset = SIGNIFICANT | frozenset({
    "budget.check",
    "budget.consumed",
    "run.completed",
    "run.started",
    "tool.invoked",
    "tool.succeeded",
})
