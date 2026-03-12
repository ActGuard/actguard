# API reference

## Client

```python
class actguard.Client(
    *,
    api_key: str | None = None,
    gateway_url: str | None = None,
    event_mode: str = "verbose",
    flush_interval_ms: int = 1000,
    max_batch_events: int = 100,
    max_batch_bytes: int = 256_000,
    max_queue_events: int = 10_000,
    timeout_s: float = 5.0,
    max_retries: int = 8,
    backoff_base_ms: int = 200,
    backoff_max_ms: int = 10_000,
)
```

Runtime entrypoint for run scopes, budget scopes, and event delivery.

Example:

```python
from actguard import Client

ag = Client(
    api_key="ag_live_agent_key",
    gateway_url="https://api.actguard.ai",
)
```

`gateway_url` is the base URL for the ActGuard gateway API. The SDK does not
hardcode a specific host; `https://api.actguard.ai` is the hosted ActGuard
gateway, and self-hosted/custom gateways can use any compatible base URL.

### Constructors

```python
Client.from_file(path: str | os.PathLike[str]) -> Client
Client.from_env() -> Client
```

`from_env()` reads `ACTGUARD_CONFIG` as either base64 JSON or a JSON file path.

### Methods

```python
client.run(user_id: str | None = None, run_id: str | None = None)
client.budget_guard(
    *,
    user_id: str | None = None,
    name: str | None = None,
    usd_limit: float | None = None,
    run_id: str | None = None,
    plan_key: str | None = None,
)
client.close() -> None
```

## Run context

`client.run(...)` returns a context manager that activates runtime state for:

- `max_attempts`
- `idempotent`
- `budget_guard`
- runtime event attribution

It supports both `with` and `async with`.

## BudgetGuard

```python
class actguard.BudgetGuard(
    *,
    client: Client,
    user_id: str | None = None,
    name: str | None = None,
    usd_limit: float | None = None,
    run_id: str | None = None,
    plan_key: str | None = None,
)
```

Although `BudgetGuard` is exported, the supported public entrypoint is `client.budget_guard(...)`.

### Properties

| Property | Type | Description |
|---|---|---|
| `user_id` | `str \| None` | Budget owner for this scope |
| `name` | `str \| None` | Optional scope label |
| `usd_limit` | `float \| None` | USD budget for this scope |
| `run_id` | `str \| None` | Active run id once entered |
| `plan_key` | `str \| None` | Optional plan identifier |
| `tokens_used` | `int` | Root totals for root scopes, local totals for nested scopes |
| `usd_used` | `float` | Root totals for root scopes, local totals for nested scopes |
| `local_tokens_used` | `int` | Tokens attributed to this scope only |
| `local_usd_used` | `float` | USD attributed to this scope only |
| `root_tokens_used` | `int` | Root-scope aggregate tokens |
| `root_usd_used` | `float` | Root-scope aggregate USD |

## Session API

```python
actguard.session(id: str, scope: dict[str, str] | None = None) -> GuardSession
```

Context manager for chain-of-custody state used by `prove` and `enforce`. Supports `with` and `async with`.

### GuardSession

```python
class actguard.session.GuardSession
```

Stores session id plus optional string-valued scope dimensions.

## Decorators

### `@rate_limit`

```python
actguard.rate_limit(
    *,
    max_calls: int = 10,
    period: float = 60.0,
    scope: str | None = None,
)
```

### `FailureKind` and presets

```python
class actguard.FailureKind(str, Enum)
actguard.FAIL_ON_DEFAULT
actguard.IGNORE_ON_DEFAULT
actguard.FAIL_ON_STRICT
actguard.FAIL_ON_INFRA_ONLY
```

### `@circuit_breaker`

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

### `@max_attempts`

```python
actguard.max_attempts(*, calls: int)
```

Requires an active `client.run(...)`.

### `@timeout`

```python
actguard.timeout(seconds: float, executor: Executor | None = None)
actguard.shutdown(wait: bool = True) -> None
```

### `@idempotent`

```python
actguard.idempotent(
    *,
    ttl_s: float = 3600,
    on_duplicate: Literal["return", "raise"] = "return",
    safe_exceptions: tuple = (),
)
```

Requires the decorated function to declare `idempotency_key` and requires an active `client.run(...)`.

### `@prove`

```python
actguard.prove(
    kind: str,
    extract: str | Callable,
    ttl: float = 300,
    max_items: int = 200,
    on_too_many: str = "block",
)
```

Requires an active `actguard.session(...)`.

### `@enforce`

```python
actguard.enforce(rules: list[Rule])
```

Requires an active `actguard.session(...)`.

### Rules

```python
actguard.RequireFact(arg: str, kind: str, hint: str = "")
actguard.Threshold(arg: str, max: float)
actguard.BlockRegex(arg: str, pattern: str)
```

### `@tool`

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

`policy` is currently reserved.

## Exceptions

Import concrete exceptions from `actguard.exceptions`.

### Top-level base classes

```python
class actguard.ActGuardError(Exception)
class actguard.ActGuardToolError(ActGuardError)
class actguard.ActGuardPaymentRequired(ActGuardError)
```

### Runtime and budget exceptions

```python
class actguard.exceptions.BudgetExceededError(ToolGuardError)
class actguard.exceptions.BudgetTransportError(ActGuardRuntimeError)
class actguard.exceptions.MissingRuntimeContextError(ActGuardRuntimeContextError)
class actguard.exceptions.NestedRuntimeContextError(ActGuardRuntimeContextError)
class actguard.exceptions.NestedBudgetGuardError(ActGuardRuntimeContextError)
class actguard.exceptions.BudgetConfigurationError(ActGuardRuntimeContextError)
class actguard.exceptions.BudgetClientMismatchError(ActGuardRuntimeContextError)
```

`BudgetExceededError` attributes:

| Attribute | Type |
|---|---|
| `user_id` | `str \| None` |
| `tokens_used` | `int` |
| `usd_used` | `float` |
| `usd_limit` | `float \| None` |
| `limit_type` | `Literal["usd"]` |
| `scope_id` | `str \| None` |
| `scope_name` | `str \| None` |
| `scope_kind` | `str \| None` |
| `parent_scope_id` | `str \| None` |
| `root_scope_id` | `str \| None` |

### Tool-guard exceptions

```python
class actguard.exceptions.RateLimitExceeded(ToolGuardError)
class actguard.exceptions.CircuitOpenError(ToolGuardError)
class actguard.exceptions.MaxAttemptsExceeded(ToolGuardError)
class actguard.exceptions.PolicyViolationError(ToolGuardError)
class actguard.exceptions.IdempotencyInProgress(ToolGuardError)
class actguard.exceptions.DuplicateIdempotencyKey(ToolGuardError)
class actguard.exceptions.IdempotencyOutcomeUnknown(ToolGuardError)
```

`PolicyViolationError` is the canonical prove/enforce failure type. Compatibility alias: `GuardError = PolicyViolationError`.

### Tool-execution and usage exceptions

```python
class actguard.exceptions.ToolExecutionError(ActGuardToolError)
class actguard.exceptions.ToolTimeoutError(ToolExecutionError)
class actguard.exceptions.InvalidIdempotentToolError(ActGuardUsageError)
class actguard.exceptions.MissingIdempotencyKeyError(ActGuardUsageError)
class actguard.exceptions.TimeoutUsageError(ActGuardUsageError)
class actguard.exceptions.ScopeValidationError(ActGuardUsageError)
class actguard.exceptions.SessionUsageError(ActGuardUsageError)
class actguard.exceptions.ReportingContractError(ActGuardUsageError)
class actguard.exceptions.CircuitBreakerConfigurationError(ActGuardUsageError)
class actguard.exceptions.MaxAttemptsConfigurationError(ActGuardUsageError)
```
