# API Reference

## BudgetGuard

```python
class actguard.BudgetGuard(
    *,
    user_id: str,
    token_limit: int | None = None,
    usd_limit: float | None = None,
)
```

Context manager that tracks cumulative token and USD usage for LLM API calls made within its block. Supports both sync (`with`) and async (`async with`) usage.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `user_id` | `str` | — | Identifier for the budget owner. Included in `BudgetExceededError` messages and on the exception object. |
| `token_limit` | `int \| None` | `None` | Maximum cumulative tokens (input + output combined). Raises `BudgetExceededError` when `tokens_used >= token_limit`. |
| `usd_limit` | `float \| None` | `None` | Maximum cumulative USD cost. Raises `BudgetExceededError` when `usd_used >= usd_limit`. |

At least one limit should be set. If both are `None`, usage is tracked but no error is ever raised.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `tokens_used` | `int` | Total tokens consumed so far (input + output across all calls). |
| `usd_used` | `float` | Total USD cost so far. |
| `user_id` | `str` | The `user_id` passed to the constructor. |
| `token_limit` | `int \| None` | The `token_limit` passed to the constructor. |
| `usd_limit` | `float \| None` | The `usd_limit` passed to the constructor. |

---

## BudgetExceededError

```python
class actguard.BudgetExceededError(Exception)
```

Raised by `BudgetGuard` when a token or USD limit is exceeded.

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `user_id` | `str` | The user ID from the active `BudgetGuard`. |
| `tokens_used` | `int` | Total tokens consumed at the moment the limit was hit. |
| `usd_used` | `float` | Total USD cost at the moment the limit was hit. |
| `token_limit` | `int \| None` | The token limit that was set (may be `None` if no token limit). |
| `usd_limit` | `float \| None` | The USD limit that was set (may be `None` if no USD limit). |
| `limit_type` | `Literal["token", "usd"]` | Which limit triggered the error. |

---

## Tool Runtime

### RunContext

```python
class actguard.RunContext(*, run_id: str | None = None)
```

Context manager for tool runtime state (`max_attempts`, `idempotent`). Supports sync and async usage.

### Constructor params

| Parameter | Type | Default | Description |
|---|---|---|---|
| `run_id` | `str \| None` | auto-generated UUID | Optional explicit run identifier used by runtime exceptions |

### Methods

| Method | Signature | Description |
|---|---|---|
| enter | `__enter__() -> RunContext` | Activate run state |
| exit | `__exit__(exc_type, exc_val, exc_tb) -> None` | Restore previous run state |
| async enter | `__aenter__() -> RunContext` | Async enter |
| async exit | `__aexit__(exc_type, exc_val, exc_tb) -> None` | Async exit |
| attempts | `get_attempt_count(tool_id: str) -> int` | Return current attempt count for a tool id in this context |

---

## Tool Guards

### configure()

```python
actguard.configure(config: str | None = None) -> None
```

Wires in the ActGuard gateway for global enforcement reporting. Call once at startup; optional.

---

### @rate_limit

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
| `max_calls` | `int` | `10` | Maximum calls allowed within `period` |
| `period` | `float` | `60.0` | Sliding-window length in seconds |
| `scope` | `str \| None` | `None` | Argument name used as counter key; `None` means one global counter |

---

### FailureKind

```python
class actguard.FailureKind(str, Enum)
```

Stable failure taxonomy used by `@circuit_breaker`.

Members:

- `TRANSPORT`
- `TIMEOUT`
- `OVERLOADED`
- `THROTTLED`
- `AUTH`
- `INVALID`
- `NOT_FOUND`
- `CONFLICT`
- `UNKNOWN`

---

### Preset constants

```python
actguard.FAIL_ON_DEFAULT
actguard.IGNORE_ON_DEFAULT
actguard.FAIL_ON_STRICT
actguard.FAIL_ON_INFRA_ONLY
```

Set-like presets of `FailureKind` values.

---

### @circuit_breaker

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

Circuit breaker decorator for sync and async functions.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Dependency name |
| `max_fails` | `int` | `3` | Counted failures before OPEN |
| `reset_timeout` | `float` | `60.0` | Seconds before calls are allowed again |
| `fail_on` | `set[FailureKind]` | `FAIL_ON_DEFAULT` | Kinds that increment/open |
| `ignore_on` | `set[FailureKind]` | `IGNORE_ON_DEFAULT` | Kinds that do not affect breaker state |

---

### @max_attempts

```python
actguard.max_attempts(*, calls: int)
```

Decorator that limits invocations per tool per active `RunContext`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `calls` | `int` | required | Max allowed attempts per run |

Raises `MissingRuntimeContextError` if no `RunContext` is active.

---

### @timeout

```python
actguard.timeout(seconds: float, executor: Executor | None = None)
```

Decorator that bounds tool wall-clock runtime.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `seconds` | `float` | required | Timeout threshold in seconds |
| `executor` | `Executor \| None` | `None` | Optional executor for sync tool execution |

Raises `ToolTimeoutError` when exceeded.

### shutdown()

```python
actguard.shutdown(wait: bool = True) -> None
```

Shuts down the shared timeout executor used by `@timeout` when no custom executor is passed.

---

### @idempotent

```python
actguard.idempotent(
    *,
    ttl_s: float = 3600,
    on_duplicate: Literal["return", "raise"] = "return",
    safe_exceptions: tuple = (),
)
```

Decorator enforcing at-most-once execution per `(tool_id, idempotency_key)` in an active `RunContext`.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `ttl_s` | `float` | `3600` | TTL for idempotency entries |
| `on_duplicate` | `"return" \| "raise"` | `"return"` | Return cached result or raise |
| `safe_exceptions` | `tuple` | `()` | Exceptions that clear state and allow retry |

Requires the decorated function to include an `idempotency_key` parameter.

---

### @tool

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

Unified decorator that composes multiple guards.

| Kwarg | Type | Description |
|---|---|---|
| `rate_limit` | `dict \| None` | `max_calls`, `period`, `scope` |
| `circuit_breaker` | `dict \| None` | `name`, `max_fails`, `reset_timeout`, `fail_on`, `ignore_on` |
| `max_attempts` | `dict \| None` | `calls` |
| `timeout` | `float \| None` | Timeout in seconds |
| `timeout_executor` | `Executor \| None` | Custom executor for sync timeout execution |
| `idempotent` | `dict \| None` | `ttl_s`, `on_duplicate`, `safe_exceptions` |
| `policy` | — | Reserved stub |

Execution order:

`idempotent -> max_attempts -> circuit_breaker -> rate_limit -> timeout -> fn`

---

## Exceptions

### ToolGuardError

```python
class actguard.ToolGuardError(Exception)
```

Base class for guard-blocked tool execution.

### ToolExecutionError

```python
class actguard.ToolExecutionError(Exception)
```

Base class for tool execution failures.

### RateLimitExceeded

```python
class actguard.RateLimitExceeded(ToolGuardError)
```

| Attribute | Type |
|---|---|
| `func_name` | `str` |
| `scope_value` | `str \| None` |
| `max_calls` | `int` |
| `period` | `float` |
| `retry_after` | `float` |

### CircuitOpenError

```python
class actguard.CircuitOpenError(ToolGuardError)
```

| Attribute | Type |
|---|---|
| `dependency_name` | `str` |
| `reset_at` | `float` |
| `retry_after` | `float` |

### MissingRuntimeContextError

```python
class actguard.MissingRuntimeContextError(ToolGuardError)
```

Raised when run-state decorators are called without `RunContext`.

### MaxAttemptsExceeded

```python
class actguard.MaxAttemptsExceeded(ToolGuardError)
```

| Attribute | Type |
|---|---|
| `run_id` | `str` |
| `tool_name` | `str` |
| `limit` | `int` |
| `used` | `int` |

### ToolTimeoutError

```python
class actguard.ToolTimeoutError(ToolExecutionError)
```

| Attribute | Type |
|---|---|
| `tool_name` | `str` |
| `timeout_s` | `float` |
| `run_id` | `str \| None` |

### InvalidIdempotentToolError

```python
class actguard.InvalidIdempotentToolError(ActGuardError)
```

Raised when a decorated function lacks `idempotency_key`.

### MissingIdempotencyKeyError

```python
class actguard.MissingIdempotencyKeyError(ToolGuardError)
```

Raised when `idempotency_key` is empty or missing.

### IdempotencyInProgress

```python
class actguard.IdempotencyInProgress(ToolGuardError)
```

Raised when the same key is currently in progress.

### DuplicateIdempotencyKey

```python
class actguard.DuplicateIdempotencyKey(ToolGuardError)
```

Raised on duplicate completed key when `on_duplicate="raise"`.

### IdempotencyOutcomeUnknown

```python
class actguard.IdempotencyOutcomeUnknown(ToolGuardError)
```

Raised when previous unsafe failure left outcome unknown until TTL expiry.
