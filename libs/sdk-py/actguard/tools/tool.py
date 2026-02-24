def tool(
    fn=None,
    *,
    rate_limit=None,
    circuit_breaker=None,
    idempotency_key=None,
    policy=None,
):
    """Unified decorator. Each kwarg maps to the corresponding standalone decorator.

    Unspecified guards are not applied. Execution order: policy → idempotency →
    rate_limit → circuit_breaker → fn.
    """
    if fn is None:
        return lambda f: tool(
            f,
            rate_limit=rate_limit,
            circuit_breaker=circuit_breaker,
            idempotency_key=idempotency_key,
            policy=policy,
        )

    wrapped = fn

    if circuit_breaker is not None:
        from .circuit_breaker import circuit_breaker as _cb

        wrapped = _cb(wrapped, **circuit_breaker)

    if rate_limit is not None:
        from .rate_limit import rate_limit as _rl

        wrapped = _rl(wrapped, **rate_limit)

    # idempotency, policy: stubs reserved for future phases

    return wrapped
