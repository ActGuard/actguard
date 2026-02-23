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
