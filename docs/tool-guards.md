# Tool Guards

## Overview

Tool Guards are decorators for protecting tool functions (the functions your agent can invoke). v0.1 includes:

- `@actguard.rate_limit` for call-rate control.
- `@actguard.circuit_breaker` for dependency-health protection.
- `@actguard.max_attempts` for per-run attempt caps.
- `@actguard.timeout` for wall-clock execution limits.
- `@actguard.idempotent` for at-most-once behavior by idempotency key.
- `@actguard.prove` for minting verified facts from read-tool results.
- `@actguard.enforce` for checking chain-of-custody rules before writes.
- `@actguard.tool(...)` as a unified decorator that composes guards.

Enforcement is local and in-process by default. If configured, ActGuard can also report checks to the gateway for global enforcement visibility.

---

## actguard.configure()

```python
actguard.configure(config: str | None = None) -> None
```

Wires in the ActGuard gateway so tool-guard checks can be reported for global enforcement. Decorators work without any configuration.

### Config fields

| Field | Type | Description |
|---|---|---|
| `agent_id` | `str` | Identifier for this agent instance |
| `gateway_url` | `str \| None` | ActGuard gateway endpoint |
| `api_key` | `str \| None` | API key for the gateway |

---

## RunContext

`max_attempts` and `idempotent` require an active run-scoped state:

```python
from actguard import RunContext

with RunContext(run_id="req-42"):
    ...
```

`RunContext` also supports async:

```python
async with RunContext(run_id="req-42"):
    ...
```

Without an active `RunContext`, these decorators raise `MissingRuntimeContextError`.

---

## @actguard.rate_limit

```python
actguard.rate_limit(
    *,
    max_calls: int = 10,
    period: float = 60.0,
    scope: str | None = None,
)
```

Decorator that enforces a sliding-window call-rate limit on sync and async functions.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_calls` | `int` | `10` | Maximum calls in the window |
| `period` | `float` | `60.0` | Window length in seconds |
| `scope` | `str \| None` | `None` | Function argument name used as key; `None` means global counter |

---

## FailureKind and presets

`@circuit_breaker` uses typed `FailureKind` values:

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

- `FAIL_ON_DEFAULT = {TRANSPORT, TIMEOUT, OVERLOADED}`
- `IGNORE_ON_DEFAULT = {INVALID, NOT_FOUND, CONFLICT}`
- `FAIL_ON_STRICT = FAIL_ON_DEFAULT | {AUTH, THROTTLED}`
- `FAIL_ON_INFRA_ONLY = {TRANSPORT, TIMEOUT}`

---

## @actguard.circuit_breaker

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

Per-decorator CLOSED/OPEN circuit breaker for sync and async functions.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Dependency name shown in open-circuit errors |
| `max_fails` | `int` | `3` | Number of counted failures before opening |
| `reset_timeout` | `float` | `60.0` | Seconds before calls are allowed again |
| `fail_on` | `set[FailureKind]` | `FAIL_ON_DEFAULT` | Kinds that increment/open |
| `ignore_on` | `set[FailureKind]` | `IGNORE_ON_DEFAULT` | Kinds that do not affect breaker state |

---

## @actguard.max_attempts

```python
actguard.max_attempts(*, calls: int)
```

Caps invocations per tool per `RunContext`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `calls` | `int` | required | Maximum number of allowed attempts per run |

Notes:

- `calls` must be an integer `>= 1`.
- Attempt count increments before the tool body runs.
- Failed executions still consume an attempt.

Example:

```python
from actguard import RunContext, max_attempts, MaxAttemptsExceeded

@max_attempts(calls=2)
def fetch_profile(user_id: str) -> dict:
    ...

with RunContext(run_id="run-a"):
    fetch_profile("u1")
    fetch_profile("u1")
    try:
        fetch_profile("u1")
    except MaxAttemptsExceeded as e:
        print(e.used, e.limit, e.run_id)
```

---

## @actguard.timeout

```python
actguard.timeout(seconds: float, executor: Executor | None = None)
```

Bounds tool invocation wall-clock duration for sync and async functions.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `seconds` | `float` | required | Timeout threshold in seconds |
| `executor` | `Executor \| None` | `None` | Optional custom executor for sync functions |

Notes:

- Raises `ToolTimeoutError` on timeout.
- Generator and async-generator functions are rejected at decoration time.
- For sync functions, execution is submitted to an executor and timeout includes queue wait time.
- If called inside `RunContext`, timeout errors include the current `run_id`.

---

## @actguard.idempotent

```python
actguard.idempotent(
    *,
    ttl_s: float = 3600,
    on_duplicate: Literal["return", "raise"] = "return",
    safe_exceptions: tuple = (),
)
```

Enforces at-most-once execution per `(tool_id, idempotency_key)` within a `RunContext`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `ttl_s` | `float` | `3600` | Lifetime of stored idempotency outcome |
| `on_duplicate` | `"return" \| "raise"` | `"return"` | Return cached result or raise on duplicates |
| `safe_exceptions` | `tuple` | `()` | Exceptions that clear state and allow retry |

Requirements and behavior:

- Decorated function must declare an `idempotency_key` parameter.
- Caller must provide a non-empty `idempotency_key`.
- Duplicate behavior for completed calls:
  - `on_duplicate="return"`: returns cached result.
  - `on_duplicate="raise"`: raises `DuplicateIdempotencyKey`.
- If a prior attempt failed with an exception not in `safe_exceptions`, retries raise `IdempotencyOutcomeUnknown` until TTL expiry.
- Concurrent in-flight duplicate calls raise `IdempotencyInProgress`.

Example:

```python
from actguard import RunContext, idempotent

@idempotent(ttl_s=600, on_duplicate="return")
def create_order(user_id: str, *, idempotency_key: str) -> str:
    ...

with RunContext():
    o1 = create_order("alice", idempotency_key="k-1")
    o2 = create_order("alice", idempotency_key="k-1")
    assert o1 == o2
```

---

## Chain-of-custody guards

### actguard.session()

`prove` and `enforce` require an active chain-of-custody session:

```python
import actguard

with actguard.session("req-42", {"user_id": "u1"}):
    ...
```

`session()` also supports async:

```python
import actguard

async with actguard.session("req-42", {"user_id": "u1"}):
    ...
```

Without an active session, `prove` and `enforce` raise `GuardError(code="NO_SESSION")`.

### @actguard.prove

```python
actguard.prove(
    kind: str,
    extract: str | Callable,
    ttl: float = 300,
    max_items: int = 200,
    on_too_many: str = "block",
)
```

Decorator that mints verified facts from a tool's return value.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `kind` | `str` | required | Fact kind/category (for example `order_id`) |
| `extract` | `str \| Callable` | required | Field/attribute name, or callable that extracts value(s) from result |
| `ttl` | `float` | `300` | Fact lifetime in seconds |
| `max_items` | `int` | `200` | Maximum minted values per tool invocation |
| `on_too_many` | `"block" \| "truncate"` | `"block"` | Block with `GuardError` or mint first `max_items` only |

Notes:

- Supports sync and async tool functions.
- Requires an active `actguard.session(...)`.
- Minted values are normalized to strings.
- `on_too_many="block"` raises `GuardError(code="TOO_MANY_RESULTS")`.

Example:

```python
import actguard

@actguard.prove(kind="order_id", extract="id")
def list_orders(user_id: str) -> list[dict]:
    ...

with actguard.session("req-1", {"user_id": "u1"}):
    list_orders("u1")
```

---

### @actguard.enforce

```python
actguard.enforce(rules: list[Rule])
```

Decorator that checks chain-of-custody rules before a tool executes.

Rules are evaluated in order. The first failing rule raises `GuardError`.

Notes:

- Supports sync and async tool functions.
- Requires an active `actguard.session(...)`.
- Uses function argument binding (including defaults) before rule evaluation.

Example:

```python
import actguard

@actguard.enforce([actguard.RequireFact("order_id", "order_id")])
def delete_order(order_id: str) -> str:
    ...
```

---

### Rule classes

#### RequireFact

```python
actguard.RequireFact(arg: str, kind: str, hint: str = "")
```

Requires argument value(s) to be previously proven in the active session.

#### Threshold

```python
actguard.Threshold(arg: str, max: float)
```

Blocks if numeric argument exceeds the configured maximum.

#### BlockRegex

```python
actguard.BlockRegex(arg: str, pattern: str)
```

Blocks if argument string matches the configured regex pattern.

---

### Prove-then-enforce pattern

```python
import actguard
from actguard import GuardError

@actguard.prove(kind="order_id", extract="id")
def list_orders(user_id: str) -> list[dict]:
    return [{"id": "o1"}]

@actguard.enforce([actguard.RequireFact("order_id", "order_id")])
def cancel_order(order_id: str) -> str:
    return f"cancelled:{order_id}"

with actguard.session("req-123", {"user_id": "alice"}):
    list_orders("alice")
    cancel_order("o1")
```

---

### In-memory store semantics

- Verified facts are stored in-memory, in-process, and are ephemeral.
- Facts are scoped by session id and scope hash.
- Data does not survive process restart and is not shared across processes.

---

## @actguard.tool (unified decorator)

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

Single decorator that composes multiple guards.

Execution order (outermost to innermost guard):

`idempotent -> max_attempts -> circuit_breaker -> rate_limit -> timeout -> fn`

Example:

```python
import actguard
from actguard import RunContext

@actguard.tool(
    idempotent={"ttl_s": 600, "on_duplicate": "return"},
    max_attempts={"calls": 3},
    rate_limit={"max_calls": 10, "period": 60, "scope": "user_id"},
    circuit_breaker={"name": "search_api", "max_fails": 3, "reset_timeout": 60},
    timeout=2.0,
)
def search_web(user_id: str, query: str, *, idempotency_key: str) -> str:
    ...

with RunContext():
    search_web("alice", "latest", idempotency_key="r-1")
```

> **Name collision note:** Many frameworks export their own `@tool`. Prefer `import actguard` and `@actguard.tool(...)`.
>
> `@actguard.tool(...)` currently composes `rate_limit`, `circuit_breaker`,
> `max_attempts`, `timeout`, and `idempotent`. Use `@actguard.prove` and
> `@actguard.enforce` as separate decorators.

---

## Exceptions

### ToolGuardError

```python
class actguard.ToolGuardError(Exception)
```

Base exception for guard-blocked execution.

### GuardError

```python
class actguard.GuardError(ToolGuardError)
```

Raised by `@prove` / `@enforce` when chain-of-custody checks fail.

Common `code` values:

- `NO_SESSION`
- `MISSING_FACT`
- `TOO_MANY_RESULTS`
- `THRESHOLD_EXCEEDED`
- `PATTERN_BLOCKED`

### ToolExecutionError

```python
class actguard.ToolExecutionError(Exception)
```

Base exception for tool-execution failures (not guard blocks).

### RateLimitExceeded

Raised when call rate exceeds `max_calls` in `period`.

| Attribute | Type | Description |
|---|---|---|
| `func_name` | `str` | Decorated function name |
| `scope_value` | `str \| None` | Runtime scope value (or global scope) |
| `max_calls` | `int` | Configured call limit |
| `period` | `float` | Configured window |
| `retry_after` | `float` | Seconds until next call is safe |

### CircuitOpenError

Raised when a breaker is OPEN and a call is short-circuited.

| Attribute | Type | Description |
|---|---|---|
| `dependency_name` | `str` | Breaker dependency name |
| `reset_at` | `float` | Epoch seconds when calls may resume |
| `retry_after` | `float` | Seconds remaining until reset |

### MissingRuntimeContextError

Raised when `max_attempts` or `idempotent` runs without an active `RunContext`.

### MaxAttemptsExceeded

Raised when calls exceed `max_attempts` limit in a run.

| Attribute | Type | Description |
|---|---|---|
| `run_id` | `str` | Active run id |
| `tool_name` | `str` | Tool identifier (`module:qualname`) |
| `limit` | `int` | Allowed calls |
| `used` | `int` | Attempts already consumed |

### ToolTimeoutError

Raised when `timeout` is exceeded. Inherits `ToolExecutionError`.

| Attribute | Type | Description |
|---|---|---|
| `tool_name` | `str` | Tool qualname |
| `timeout_s` | `float` | Configured timeout in seconds |
| `run_id` | `str \| None` | Run id if called inside `RunContext` |

### InvalidIdempotentToolError

Raised at decoration time if the function lacks an `idempotency_key` parameter.

### MissingIdempotencyKeyError

Raised when `idempotency_key` is missing, empty, or `None`.

### IdempotencyInProgress

Raised when same `(tool, key)` is already running.

### DuplicateIdempotencyKey

Raised when duplicate key is encountered and `on_duplicate="raise"`.

### IdempotencyOutcomeUnknown

Raised when a previous unsafe failure left outcome unknown until TTL expiry.

---

## Stacking order with frameworks

Keep framework decorators outermost and actguard decorators innermost.

```python
# CORRECT
@framework_tool
@actguard.rate_limit(max_calls=5, period=60, scope="user_id")
@actguard.circuit_breaker(name="mail_api")
def send_email(user_id: str, subject: str) -> str:
    ...

# WRONG
@actguard.circuit_breaker(name="mail_api")
@framework_tool
def send_email(...):
    ...
```

If you use `max_attempts` or `idempotent`, execute tools under `RunContext`.

---

## Framework integrations

Pattern is consistent: framework decorator outermost, actguard innermost.

### LangChain / LangGraph

```python
from langchain_core.tools import tool
import actguard

@tool
@actguard.rate_limit(max_calls=10, period=60, scope="user_id")
@actguard.circuit_breaker(name="crm_api")
def fetch_customer(user_id: str) -> dict:
    ...
```

### Pydantic AI

```python
from pydantic_ai import Agent
import actguard

agent = Agent("openai:gpt-4o")

@agent.tool
@actguard.timeout(1.5)
@actguard.circuit_breaker(name="mailer")
async def send_email(ctx, user_id: str, subject: str) -> str:
    ...
```

### OpenAI Agents SDK

```python
from agents import function_tool
import actguard

@function_tool
@actguard.tool(
    max_attempts={"calls": 3},
    timeout=2.0,
    circuit_breaker={"name": "ticketing_api", "max_fails": 3},
)
def create_ticket(user_id: str, title: str) -> str:
    ...
```
