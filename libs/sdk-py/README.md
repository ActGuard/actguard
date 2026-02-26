# ActGuard Python SDK

> Drop-in action firewall for LLM agents.

## Installation

```bash
pip install actguard
# or
uv add actguard
```

## Why agents break (and what ActGuard prevents)

| Real-world problem | What actually happens | ActGuard |
|--------------------|----------------------|----------|
| Made-up data | Agent uses an ID it never fetched | ✅ |
| Lost context | Correct ID fetched → wrong one used later | ✅ |
| Endless retries | Same tool called over and over with tiny changes | ✅ |
| Runaway costs | Agent keeps exploring and silently spends | ✅ |
| Skipped workflow steps | Performs side effect before required step | ✅ |
| Obeying malicious input | Untrusted text tells it to do something destructive | ✅ |

## Set a spending or token limit (BudgetGuard)

Stop spending as soon as a user's request crosses $0.05:

```python
from actguard import BudgetGuard, BudgetExceededError
import openai

client = openai.OpenAI()

try:
    with BudgetGuard(user_id="alice", usd_limit=0.05) as guard:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Summarise the history of Rome."}],
        )
        print(response.choices[0].message.content)
except BudgetExceededError as e:
    print(f"Budget hit: {e}")
finally:
    print(f"Spent ${guard.usd_used:.6f} using {guard.tokens_used} tokens")
```

Set a token limit instead, or combine both — either limit triggers the error, whichever is hit first:

```python
with BudgetGuard(user_id="bob", token_limit=1_000) as guard:
    ...

with BudgetGuard(user_id="carol", token_limit=5_000, usd_limit=0.10) as guard:
    ...
```

`BudgetGuard` is also an async context manager:

```python
import asyncio
import openai
from actguard import BudgetGuard

async def main():
    client = openai.AsyncOpenAI()
    async with BudgetGuard(user_id="dave", usd_limit=0.10) as guard:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello!"}],
        )
    print(f"Used ${guard.usd_used:.4f}")

asyncio.run(main())
```

Streaming responses are fully supported — actguard wraps the iterator transparently and captures the usage chunk emitted at the end of the stream:

```python
with BudgetGuard(user_id="eve", usd_limit=0.10) as guard:
    stream = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Tell me a story."}],
        stream=True,
    )
    for chunk in stream:
        if chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)

print(f"\nUsed ${guard.usd_used:.4f}")
```

## Rate-limit a tool

Add a per-user rate limit to any tool function with a single decorator:

```python
from actguard import rate_limit, RateLimitExceeded

@rate_limit(max_calls=5, period=60, scope="user_id")
def send_email(user_id: str, subject: str) -> str:
    ...

try:
    send_email("alice", "Hello!")
except RateLimitExceeded as e:
    print(f"Slow down, retry in {e.retry_after:.0f}s")
```

`scope="user_id"` means each distinct `user_id` gets its own counter. Omit `scope` for one global counter.

## Circuit-break a tool

Add a dependency-health breaker so repeated infra failures short-circuit quickly:

```python
from actguard import circuit_breaker, CircuitOpenError

@circuit_breaker(name="postgres", max_fails=3, reset_timeout=60)
def write_order(order_id: str) -> None:
    ...

try:
    write_order("ord_123")
except CircuitOpenError as e:
    print(f"{e.dependency_name} open; retry in {e.retry_after:.1f}s")
```

## Time-bound a tool

Use `timeout` to bound wall-clock runtime for sync or async tools:

```python
from actguard import timeout, ToolTimeoutError

@timeout(1.5)
def call_slow_dependency() -> str:
    ...

try:
    call_slow_dependency()
except ToolTimeoutError as e:
    print(f"{e.tool_name} exceeded {e.timeout_s}s")
```

## Deduplicate with idempotency keys

Use `idempotent` to enforce at-most-once execution per `(tool, idempotency_key)` in a run:

```python
from actguard import RunContext, idempotent

@idempotent(ttl_s=600)
def create_invoice(user_id: str, amount_cents: int, *, idempotency_key: str) -> str:
    ...

with RunContext():
    invoice_id = create_invoice("alice", 5000, idempotency_key="inv-42")
    same_invoice_id = create_invoice("alice", 5000, idempotency_key="inv-42")
```

`max_attempts` and `idempotent` rely on run-scoped state, so they require an active `RunContext`:

```python
from actguard import RunContext, max_attempts

@max_attempts(calls=2)
def lookup_customer(customer_id: str) -> dict:
    ...

with RunContext(run_id="req-123"):
    lookup_customer("cus_1")
    lookup_customer("cus_1")
```

## Prove then enforce (chain-of-custody)

Use `prove` on read tools to mint verified facts, then `enforce` on write tools to require read-before-write:

```python
import actguard

@actguard.prove(kind="order_id", extract="id")
def list_orders(user_id: str) -> list[dict]:
    return [{"id": "o1"}]

@actguard.enforce([actguard.RequireFact("order_id", "order_id")])
def delete_order(order_id: str) -> str:
    return f"deleted:{order_id}"

with actguard.session("req-9", {"user_id": "alice"}):
    list_orders("alice")
    delete_order("o1")
```

If a write references an unproven id, `enforce` raises `GuardError` with code `MISSING_FACT`.

`prove`/`enforce` use a chain-of-custody session, so they require `actguard.session(...)`. Use `RunContext` for `max_attempts`/`idempotent`.

## Combine guards with @actguard.tool

Use the unified decorator when you want one declaration:

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
    search_web("alice", "latest earnings", idempotency_key="req-1")
```

## Which guard should I use?

- Use `rate_limit` to cap request volume per window.
- Use `circuit_breaker` to stop hammering unhealthy dependencies.
- Use `max_attempts` to cap retries/loops per run.
- Use `timeout` to bound wall-clock latency.
- Use `idempotent` to deduplicate side-effectful tools.
- Use `prove` + `enforce` to require read-before-write chain-of-custody.

## Configure actguard (optional)

`actguard.configure()` wires in the ActGuard gateway so tool-guard checks can also be reported for global enforcement across processes. Decorators work with no configuration.

Three ways to provide config:

- **JSON file path**: pass a file containing `agent_id`, `gateway_url`, and `api_key`.
- **Base64 JSON string**: pass a base64-encoded version of the same JSON.
- **`ACTGUARD_CONFIG` env var**: set the variable and call `configure()` with no args.

```python
import os
import actguard

# From a JSON file
actguard.configure("./actguard.json")

# From a base64 env var
actguard.configure(os.environ["ACTGUARD_CONFIG"])

# Or read ACTGUARD_CONFIG directly
actguard.configure()

# Clear config
actguard.configure(None)
```

## SDK Compatibility

The low-level monkey patches in `actguard.integrations` currently support these
minimum SDK versions:

- OpenAI Python SDK: `openai>=1.76.0`
- Google GenAI SDK: `google-genai>=0.8.0`
- Anthropic Python SDK: `anthropic>=0.83.0`

OpenAI minimum is also enforced by a runtime warning in
`actguard/integrations/openai.py`.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check .
ruff format .
```
