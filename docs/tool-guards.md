# Tool guards

## Overview

ActGuard protects tool functions with two kinds of scope:

- `client.run(...)` for run-scoped decorators such as `max_attempts` and `idempotent`
- `actguard.session(...)` for chain-of-custody decorators such as `prove` and `enforce`

Concrete guard exceptions live under `actguard.exceptions`.

## Run-scoped decorators

`max_attempts` and `idempotent` require an active run:

```python
import actguard

client = actguard.Client.from_env()

with client.run(run_id="req-42"):
    ...
```

Without an active run, these decorators raise `MissingRuntimeContextError`.

### `@actguard.rate_limit`

```python
actguard.rate_limit(
    *,
    max_calls: int = 10,
    period: float = 60.0,
    scope: str | None = None,
)
```

Use `scope` to key the sliding window by a function argument such as `user_id`.

### `FailureKind` and presets

`@circuit_breaker` classifies failures with `FailureKind` values:

- `TRANSPORT`
- `TIMEOUT`
- `OVERLOADED`
- `THROTTLED`
- `AUTH`
- `INVALID`
- `NOT_FOUND`
- `CONFLICT`
- `UNKNOWN`

Preset sets:

- `FAIL_ON_DEFAULT`
- `IGNORE_ON_DEFAULT`
- `FAIL_ON_STRICT`
- `FAIL_ON_INFRA_ONLY`

### `@actguard.circuit_breaker`

```python
actguard.circuit_breaker(
    *,
    name: str,
    max_fails: int = 3,
    reset_timeout: float = 60.0,
    fail_on: set[FailureKind] = FAIL_ON_DEFAULT,
    ignore_on: set[FailureKind] = IGNORE_ON_DEFAULT,
)
```

Use this to short-circuit calls when a dependency is repeatedly failing in counted categories.

### `@actguard.max_attempts`

```python
actguard.max_attempts(*, calls: int)
```

Example:

```python
import actguard
from actguard import max_attempts
from actguard.exceptions import MaxAttemptsExceeded

client = actguard.Client.from_env()

@max_attempts(calls=2)
def fetch_profile(user_id: str) -> dict:
    ...

with client.run(run_id="run-a"):
    fetch_profile("u1")
    fetch_profile("u1")
    try:
        fetch_profile("u1")
    except MaxAttemptsExceeded as e:
        print(e.used, e.limit, e.run_id)
```

### `@actguard.timeout`

```python
actguard.timeout(seconds: float, executor: Executor | None = None)
```

This bounds wall-clock duration for sync and async tools. If a run is active, timeout failures include the current `run_id`.

### `@actguard.idempotent`

```python
actguard.idempotent(
    *,
    ttl_s: float = 3600,
    on_duplicate: Literal["return", "raise"] = "return",
    safe_exceptions: tuple = (),
)
```

Requirements:

- the decorated function must declare `idempotency_key`
- callers must supply a non-empty `idempotency_key`

Example:

```python
import actguard
from actguard import idempotent

client = actguard.Client.from_env()

@idempotent(ttl_s=600, on_duplicate="return")
def create_order(user_id: str, *, idempotency_key: str) -> str:
    ...

with client.run():
    o1 = create_order("alice", idempotency_key="k-1")
    o2 = create_order("alice", idempotency_key="k-1")
    assert o1 == o2
```

## Chain-of-custody guards

### `actguard.session(...)`

`prove` and `enforce` require an active session:

```python
import actguard

with actguard.session("req-42", {"user_id": "u1"}):
    ...
```

Sessions also support `async with`.

Without an active session, `prove` and `enforce` raise `PolicyViolationError` with code `NO_SESSION`.

### `@actguard.prove`

```python
actguard.prove(
    kind: str,
    extract: str | Callable,
    ttl: float = 300,
    max_items: int = 200,
    on_too_many: str = "block",
)
```

This mints verified facts from a tool result. When `on_too_many="block"`, exceeding `max_items` raises `PolicyViolationError` with code `TOO_MANY_RESULTS`.

### `@actguard.enforce`

```python
actguard.enforce(rules: list[Rule])
```

Rules are checked before the tool body runs. The first failing rule raises `PolicyViolationError`.

### Rule classes

```python
actguard.RequireFact(arg: str, kind: str, hint: str = "")
actguard.Threshold(arg: str, max: float)
actguard.BlockRegex(arg: str, pattern: str)
```

### Prove-then-enforce pattern

```python
import actguard
from actguard.exceptions import PolicyViolationError

@actguard.prove(kind="order_id", extract="id")
def list_orders(user_id: str) -> list[dict]:
    return [{"id": "o1"}]

@actguard.enforce([actguard.RequireFact("order_id", "order_id")])
def cancel_order(order_id: str) -> str:
    return f"cancelled:{order_id}"

try:
    with actguard.session("req-123", {"user_id": "alice"}):
        list_orders("alice")
        cancel_order("o1")
except PolicyViolationError as e:
    print(e.to_prompt())
```

Fact state is in-memory, process-local, and scoped by session id plus scope hash.

## `@actguard.tool`

```python
actguard.tool(
    *,
    rate_limit: dict | None = None,
    circuit_breaker: dict | None = None,
    max_attempts: dict | None = None,
    timeout: float | None = None,
    timeout_executor: Executor | None = None,
    idempotent: dict | None = None,
    policy: ... = None,
)
```

Execution order:

`idempotent -> max_attempts -> circuit_breaker -> rate_limit -> timeout -> fn`

Example:

```python
import actguard

client = actguard.Client.from_env()

@actguard.tool(
    idempotent={"ttl_s": 600, "on_duplicate": "return"},
    max_attempts={"calls": 3},
    rate_limit={"max_calls": 10, "period": 60, "scope": "user_id"},
    circuit_breaker={"name": "search_api", "max_fails": 3, "reset_timeout": 60},
    timeout=2.0,
)
def search_web(user_id: str, query: str, *, idempotency_key: str) -> str:
    ...

with client.run():
    search_web("alice", "latest", idempotency_key="r-1")
```

`policy` is currently a reserved stub. `@actguard.tool(...)` does not compose `prove` or `enforce`; keep those as separate decorators.

## Exception guide

Import concrete exceptions from `actguard.exceptions`:

- `RateLimitExceeded`
- `CircuitOpenError`
- `MaxAttemptsExceeded`
- `ToolTimeoutError`
- `MissingRuntimeContextError`
- `PolicyViolationError`
- `InvalidIdempotentToolError`
- `MissingIdempotencyKeyError`
- `IdempotencyInProgress`
- `DuplicateIdempotencyKey`
- `IdempotencyOutcomeUnknown`

If you need a broad catch for blocked or failed tool paths, catch `ActGuardToolError`.

## Stacking order with frameworks

Keep framework decorators outermost and ActGuard decorators innermost.

```python
# Correct
@framework_tool
@actguard.rate_limit(max_calls=5, period=60, scope="user_id")
@actguard.circuit_breaker(name="mail_api")
def send_email(user_id: str, subject: str) -> str:
    ...

# Wrong
@actguard.circuit_breaker(name="mail_api")
@framework_tool
def send_email(...):
    ...
```

If you use `max_attempts` or `idempotent`, execute tools under `client.run(...)`.
