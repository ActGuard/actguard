def tool(
    fn=None,
    *,
    rate_limit=None,
    circuit_breaker=None,
    max_attempts=None,
    timeout=None,
    timeout_executor=None,
    idempotent=None,
    policy=None,
):
    """Unified decorator. Each kwarg maps to the corresponding standalone decorator.

    Unspecified guards are not applied. Execution order:
    idempotent → max_attempts → circuit_breaker → rate_limit → timeout → fn.
    """
    if fn is None:
        return lambda f: tool(
            f,
            rate_limit=rate_limit,
            circuit_breaker=circuit_breaker,
            max_attempts=max_attempts,
            timeout=timeout,
            timeout_executor=timeout_executor,
            idempotent=idempotent,
            policy=policy,
        )

    wrapped = fn

    if timeout is not None:
        from .timeout import timeout as _to

        wrapped = _to(timeout, executor=timeout_executor)(wrapped)

    if circuit_breaker is not None:
        from .circuit_breaker import circuit_breaker as _cb

        wrapped = _cb(wrapped, **circuit_breaker)

    if rate_limit is not None:
        from .rate_limit import rate_limit as _rl

        wrapped = _rl(wrapped, **rate_limit)

    if max_attempts is not None:
        from .max_attempts import max_attempts as _ma

        wrapped = _ma(wrapped, **max_attempts)

    if idempotent is not None:
        from .idempotent import idempotent as _idem

        wrapped = _idem(wrapped, **idempotent)

    # policy: stub reserved for future phases

    return wrapped
