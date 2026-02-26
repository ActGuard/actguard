# Overview

**actguard** is a lightweight Python SDK that enforces token and cost budgets across LLM API calls without changing your existing client code.

It works by patching the official OpenAI, Anthropic, and Google Generative AI SDKs at the transport layer. Wrap any block of code in a `BudgetGuard` context manager and actguard transparently counts tokens and USD spend in real time, raising `BudgetExceededError` the moment a limit is hit.

## Installation

```bash
pip install actguard
```

## Quickstart

```python
from actguard import BudgetGuard
import openai

client = openai.OpenAI()

with BudgetGuard(user_id="alice", token_limit=1_000) as guard:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Hello!"}],
    )

print(f"Used ${guard.usd_used:.4f} of ${guard.usd_limit:.2f}")
```

No configuration file, no proxy, no side-car process. Budget state lives in a Python [`ContextVar`](https://docs.python.org/3/library/contextvars.html), so it is isolated per async task and per thread.

## Key features

- **Token and USD limits**: set one, the other, or both.
- **Zero code changes to LLM calls**: patch is applied once when entering the `with` block.
- **Streaming support**: usage is captured from final stream chunks; stream contents are untouched.
- **Async support**: `BudgetGuard` is both a sync and async context manager.
- **Multi-provider**: OpenAI, Anthropic, Google Generative AI out of the box.
- **Context-var isolation**: nested or concurrent guards do not interfere.
- **Tool guards**: `rate_limit`, `circuit_breaker`, `max_attempts`, `timeout`, `idempotent`, plus `prove`/`enforce` chain-of-custody decorators.
- **Gateway-ready**: optionally report tool checks to the ActGuard platform.

## How it works

```
your code
  └── BudgetGuard.__enter__()
        ├── patches SyncAPIClient.request  (OpenAI)
        ├── patches Messages.create        (Anthropic)
        └── patches GenerativeModel.generate_content  (Google)

each patched call:
  1. pre-check: raise BudgetExceededError if already over limit
  2. forward to original SDK method
  3. read usage from response / stream
  4. accumulate tokens_used + usd_used on the BudgetState ContextVar
  5. post-check: raise BudgetExceededError if now over limit
```

## Next steps

- [Getting Started](./getting-started.md) - installation options and first examples
- [Core Concepts](./concepts.md) - limits, context isolation, streaming, and tool runtime context
- [Tool Guards](./tool-guards.md) - rate limiting, circuit breaker, max attempts, timeout, idempotency, chain-of-custody, and framework integrations
- [Integrations](./integrations/openai.md) - provider-specific notes and requirements
- [API Reference](./api-reference.md) - full API and exception reference
