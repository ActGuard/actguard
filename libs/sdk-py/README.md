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

## Set a spending limit (`client.budget_guard`)

Stop spending as soon as a user's request crosses $0.05:

```python
from actguard import Client
from actguard.exceptions import (
    ActGuardPaymentRequired,
    BudgetExceededError,
    BudgetTransportError,
)
import openai

ag = Client.from_file("./actguard.json")
oai = openai.OpenAI()
guard = None

try:
    with ag.run(user_id="alice"):
        with ag.budget_guard(usd_limit=0.05) as g:
            guard = g
            response = oai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Summarise the history of Rome."}],
            )
            print(response.choices[0].message.content)
except BudgetExceededError as e:
    print(f"Budget hit: {e}")
except ActGuardPaymentRequired as e:
    print(f"Billing rejected reserve/settle: {e}")
except BudgetTransportError as e:
    print(f"Budget transport failed: {e}")
finally:
    if guard is not None:
        print(f"Spent ${guard.usd_used:.6f} using {guard.tokens_used} tokens")
```

Under the hood, `client.budget_guard(...)` reserves on enter (`POST /api/v1/reserve`)
and settles on exit (`POST /api/v1/settle`) with your configured API key.

Set different USD limits for different scopes:

```python
with ag.run(user_id="bob"):
    with ag.budget_guard(usd_limit=0.02) as guard:
        ...

with ag.run(user_id="carol"):
    with ag.budget_guard(usd_limit=0.10) as guard:
        ...
```

You can also layer budget scope on a run scope:

```python
with ag.run(user_id="alice"):
    with ag.budget_guard(usd_limit=0.05):
        ...
```

`client.budget_guard(...)` is also an async context manager:

```python
import asyncio
import openai
from actguard import Client

async def main():
    ag = Client.from_file("./actguard.json")
    oai = openai.AsyncOpenAI()
    async with ag.run(user_id="dave"):
        async with ag.budget_guard(usd_limit=0.10) as guard:
            response = await oai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Hello!"}],
            )
    print(f"Used ${guard.usd_used:.4f}")

asyncio.run(main())
```

Streaming responses are fully supported — actguard wraps the iterator transparently and captures the usage chunk emitted at the end of the stream:

```python
with ag.run(user_id="eve"):
    with ag.budget_guard(usd_limit=0.10) as guard:
        stream = oai.chat.completions.create(
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
from actguard import rate_limit
from actguard.exceptions import RateLimitExceeded

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
from actguard import circuit_breaker
from actguard.exceptions import CircuitOpenError

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
from actguard import timeout
from actguard.exceptions import ToolTimeoutError

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
import actguard
from actguard import idempotent

@idempotent(ttl_s=600)
def create_invoice(user_id: str, amount_cents: int, *, idempotency_key: str) -> str:
    ...

client = actguard.Client.from_file("./actguard.json")
with client.run(user_id="alice"):
    invoice_id = create_invoice("alice", 5000, idempotency_key="inv-42")
    same_invoice_id = create_invoice("alice", 5000, idempotency_key="inv-42")
```

`max_attempts` and `idempotent` rely on run-scoped state, so they require an active `client.run(...)` context:

```python
import actguard
from actguard import max_attempts

@max_attempts(calls=2)
def lookup_customer(customer_id: str) -> dict:
    ...

client = actguard.Client.from_file("./actguard.json")
with client.run(run_id="req-123"):
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

If a write references an unproven id, `enforce` raises `PolicyViolationError` with code `MISSING_FACT`.

`prove`/`enforce` use a chain-of-custody session, so they require `actguard.session(...)`. Use `client.run(...)` for `max_attempts`/`idempotent`.

## Combine guards with @actguard.tool

Use the unified decorator when you want one declaration:

```python
import actguard

@actguard.tool(
    idempotent={"ttl_s": 600, "on_duplicate": "return"},
    max_attempts={"calls": 3},
    rate_limit={"max_calls": 10, "period": 60, "scope": "user_id"},
    circuit_breaker={"name": "search_api", "max_fails": 3, "reset_timeout": 60},
    timeout=2.0,
)
def search_web(user_id: str, query: str, *, idempotency_key: str) -> str:
    ...

client = actguard.Client.from_file("./actguard.json")
with client.run():
    search_web("alice", "latest earnings", idempotency_key="req-1")
```

## Which guard should I use?

- Use `rate_limit` to cap request volume per window.
- Use `circuit_breaker` to stop hammering unhealthy dependencies.
- Use `max_attempts` to cap retries/loops per run.
- Use `timeout` to bound wall-clock latency.
- Use `idempotent` to deduplicate side-effectful tools.
- Use `prove` + `enforce` to require read-before-write chain-of-custody.

## Create a client

Use `actguard.Client` as the runtime entry point. If you provide gateway/API settings, events can be shipped to ActGuard.

Two common ways to build a client:

- **JSON file path**: create a file containing `gateway_url` and `api_key`.
- **`ACTGUARD_CONFIG` env var**: set a base64 JSON blob or a JSON file path and call `Client.from_env()`.

```python
import os
import actguard

# From a JSON file
client = actguard.Client.from_file("./actguard.json")

# From ACTGUARD_CONFIG (base64 JSON or file path)
client = actguard.Client.from_env()

# Use as canonical runtime context
with client.run(user_id="alice"):
    ...
```

## Default observability

Inside `client.run(...)`, ActGuard emits runtime-scoped observability events for:

- `tool.failure`
- `guard.blocked`
- `guard.intervention`

Outside `client.run(...)`, SDK event emission is a no-op.

Per-invocation success noise (`tool.invoked`, `tool.succeeded`) is off by default.
Set `ACTGUARD_EMIT_ALL_TOOL_RUNS=1` to opt in.

When model/usage/cost data is known, emitted envelopes use a canonical snake_case
shape and promote first-class reporting fields to the top level, including
`provider`, `model`, `usd_micros`, `input_tokens`, `cached_input_tokens`,
`output_tokens`, and scope attribution fields.

Successful provider calls also emit one canonical attributed spend event:
`llm.usage`. This event powers spend-by-scope/tool reporting and does not replace
the deterministic reserve/settle ledger.

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
