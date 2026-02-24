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

These are readable after the context block exits (or during it):

| Property | Type | Description |
|----------|------|-------------|
| `tokens_used` | `int` | Total tokens consumed so far (input + output across all calls). |
| `usd_used` | `float` | Total USD cost so far. |
| `user_id` | `str` | The `user_id` passed to the constructor. |
| `token_limit` | `int \| None` | The `token_limit` passed to the constructor. |
| `usd_limit` | `float \| None` | The `usd_limit` passed to the constructor. |

### Sync context manager

```python
with BudgetGuard(user_id="alice", usd_limit=0.10) as guard:
    ...

print(guard.usd_used)
```

### Async context manager

```python
async with BudgetGuard(user_id="alice", usd_limit=0.10) as guard:
    ...

print(guard.usd_used)
```

### Example

```python
from actguard import BudgetGuard, BudgetExceededError

try:
    with BudgetGuard(user_id="alice", token_limit=500, usd_limit=0.05) as guard:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello!"}],
        )
except BudgetExceededError as e:
    print(f"Stopped: {e.limit_type} limit hit")
finally:
    print(f"Tokens: {guard.tokens_used}  USD: ${guard.usd_used:.6f}")
```

---

## BudgetExceededError

```python
class actguard.BudgetExceededError(Exception)
```

Raised by `BudgetGuard` when a token or USD limit is exceeded. The exception message is human-readable; detailed attributes are available for programmatic handling.

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `user_id` | `str` | The user ID from the active `BudgetGuard`. |
| `tokens_used` | `int` | Total tokens consumed at the moment the limit was hit. |
| `usd_used` | `float` | Total USD cost at the moment the limit was hit. |
| `token_limit` | `int \| None` | The token limit that was set (may be `None` if no token limit). |
| `usd_limit` | `float \| None` | The USD limit that was set (may be `None` if no USD limit). |
| `limit_type` | `Literal["token", "usd"]` | Which limit triggered the error. |

### Example

```python
from actguard import BudgetExceededError

try:
    with BudgetGuard(user_id="bob", usd_limit=0.01) as guard:
        client.chat.completions.create(...)
except BudgetExceededError as e:
    match e.limit_type:
        case "usd":
            print(f"Cost limit: spent ${e.usd_used:.4f} of ${e.usd_limit:.4f}")
        case "token":
            print(f"Token limit: used {e.tokens_used} of {e.token_limit}")
```

---

## Tool Guards

### configure()

```python
actguard.configure(config: str | None = None) -> None
```

Wires in the ActGuard gateway for global enforcement reporting. Call once at startup; optional.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `config` | `str \| None` | JSON file path, base64-encoded JSON string, or `None` to clear. If `None` and `ACTGUARD_CONFIG` is set, that value is used. |

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

#### Parameters

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

Set-like presets of `FailureKind` values:

- `FAIL_ON_DEFAULT = {TRANSPORT, TIMEOUT, OVERLOADED}`
- `IGNORE_ON_DEFAULT = {INVALID, NOT_FOUND, CONFLICT}`
- `FAIL_ON_STRICT = FAIL_ON_DEFAULT | {AUTH, THROTTLED}`
- `FAIL_ON_INFRA_ONLY = {TRANSPORT, TIMEOUT}`

Example customization:

```python
from actguard import FailureKind, FAIL_ON_DEFAULT

fail_on = FAIL_ON_DEFAULT | {FailureKind.AUTH}
```

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

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Dependency name |
| `max_fails` | `int` | `3` | Counted failures before OPEN |
| `reset_timeout` | `float` | `60.0` | Seconds before calls are allowed again |
| `fail_on` | `set[FailureKind]` | `FAIL_ON_DEFAULT` | Kinds that increment/open |
| `ignore_on` | `set[FailureKind]` | `IGNORE_ON_DEFAULT` | Kinds that do not affect breaker state |

---

### @tool

```python
actguard.tool(
    *,
    rate_limit: dict | None = None,
    circuit_breaker: dict | None = None,
    idempotency_key: ... ,
    policy: ... ,
)
```

Unified decorator that composes multiple guards.

#### Kwargs

| Kwarg | Type | Description |
|---|---|---|
| `rate_limit` | `dict \| None` | Rate-limit config: `max_calls`, `period`, `scope` |
| `circuit_breaker` | `dict \| None` | Circuit-breaker config: `name`, `max_fails`, `reset_timeout`, `fail_on`, `ignore_on` |
| `idempotency_key` | — | Reserved; not yet active |
| `policy` | — | Reserved; not yet active |

---

### ToolGuardError

```python
class actguard.ToolGuardError(Exception)
```

Base exception class for tool-guard failures. Catch this for generic guard handling.

---

### RateLimitExceeded

```python
class actguard.RateLimitExceeded(ToolGuardError)
```

Raised when a `@rate_limit`-decorated function exceeds its call limit.

#### Attributes

| Attribute | Type | Description |
|---|---|---|
| `func_name` | `str` | Name of the decorated function |
| `scope_value` | `str \| None` | Runtime value of the scoped argument, or global scope |
| `max_calls` | `int` | Configured call limit |
| `period` | `float` | Configured window in seconds |
| `retry_after` | `float` | Seconds until next call is safe |

---

### CircuitOpenError

```python
class actguard.CircuitOpenError(ToolGuardError)
```

Raised when a `@circuit_breaker` is OPEN and a call is short-circuited.

#### Attributes

| Attribute | Type | Description |
|---|---|---|
| `dependency_name` | `str` | Breaker dependency name |
| `reset_at` | `float` | Epoch seconds when the breaker can be retried |
| `retry_after` | `float` | Seconds until reset |
