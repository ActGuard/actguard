# Core Concepts

## BudgetGuard

`BudgetGuard` is a context manager that tracks cumulative token and USD usage for every LLM call made inside its `with` block.

```python
from actguard import BudgetGuard

with BudgetGuard(user_id="alice", usd_limit=0.10) as guard:
    # all LLM calls here are tracked
    ...

# guard.tokens_used and guard.usd_used are available after exit
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `user_id` | `str` | Identifier for the budget owner. Included in `BudgetExceededError` messages. |
| `token_limit` | `int \| None` | Max total tokens (input + output). `None` means no limit. |
| `usd_limit` | `float \| None` | Max total USD cost. `None` means no limit. |

At least one limit is recommended. If both are `None`, actguard still tracks usage, but never raises.

### Post-context properties

After the `with` block exits, `guard.tokens_used` and `guard.usd_used` are still readable:

```python
with BudgetGuard(user_id="alice", usd_limit=1.00) as guard:
    ...

print(guard.tokens_used)  # total tokens consumed
print(guard.usd_used)     # total USD cost
```

---

## Limits

### How limits are checked

Limits are checked **before and after** every LLM call:

1. **Pre-check** — if the accumulated usage already meets or exceeds the limit at the start of a call, `BudgetExceededError` is raised immediately. The SDK call is never made.
2. **Post-check** — after the response (or stream) is fully read, usage is added and the limit is checked again.

This means the error is raised at the earliest detectable moment. For a non-streaming call, that's synchronously on return. For a streaming call, it's after the final usage chunk is yielded.

### Token counting

Tokens are counted as `input_tokens + output_tokens` reported by the provider. The exact fields vary by provider — actguard normalises them internally.

### USD cost

USD cost is computed from a built-in pricing table keyed by provider and model name. If a model is not in the table, cost is recorded as `$0.00` and a `UserWarning` is emitted. You can open an issue or PR to add missing models.

---

## Context isolation

Budget state lives in a [`ContextVar`](https://docs.python.org/3/library/contextvars.html). This gives strong isolation guarantees:

- **Threads** — each thread has its own context; concurrent guards don't bleed into each other.
- **Async tasks** — each `asyncio` task inherits a copy of the context at creation time. A guard started in one task is invisible to a sibling task.
- **Nested guards** — inner guards don't inherit the outer guard's accumulated totals.

```python
with BudgetGuard(user_id="outer", usd_limit=1.00) as outer:
    call_llm()  # counted toward outer

    with BudgetGuard(user_id="inner", usd_limit=0.05) as inner:
        call_llm()  # counted toward inner only

    # outer still tracks its own calls; inner's usage is isolated
```

---

## Tool runtime context

`RunContext` provides per-run state for tool decorators that need cross-call memory.

```python
from actguard import RunContext

with RunContext(run_id="req-123"):
    ...
```

### What state is stored

- Attempt counters per tool (`max_attempts`)
- Idempotency records per `(tool_id, idempotency_key)` (`idempotent`)
- Run identifier used by related exceptions

### Isolation semantics

- **Per run**: a new `RunContext` starts with fresh attempt counters and idempotency state.
- **Nested contexts**: inner `RunContext` has independent state; on exit, outer state is restored.
- **Async support**: `RunContext` supports `with` and `async with`.

`timeout` does not require `RunContext`, but if one is active it includes `run_id` in `ToolTimeoutError`.

---

## Patching

`BudgetGuard.__enter__()` calls `patch_all()` once per process, which monkey-patches the transport layer of each installed LLM SDK. The patch is idempotent — calling it multiple times has no effect.

The patch is **transparent**: if no `BudgetGuard` is active in the current context, patched methods behave exactly like the originals.

See the [Integrations](./integrations/openai.md) section for provider-specific details and version requirements.

---

## BudgetExceededError

Raised when a limit is hit. Inherits from `Exception`.

```python
from actguard import BudgetExceededError

try:
    with BudgetGuard(user_id="alice", usd_limit=0.01) as guard:
        client.chat.completions.create(...)
except BudgetExceededError as e:
    print(e.limit_type)    # "token" or "usd"
    print(e.tokens_used)
    print(e.usd_used)
    print(e.usd_limit)
```

Full attribute reference: [API Reference → BudgetExceededError](./api-reference.md#budgetexceedederror).
