# Getting started

## Requirements

- Python 3.9+
- At least one supported LLM SDK installed
- Gateway config for reserve/settle-backed budget scopes, typically via `ACTGUARD_CONFIG`

## Install actguard

```bash
pip install actguard
```

## Create a client

Use `Client` as the runtime entrypoint.

```python
from actguard import Client

client = Client.from_env()
# or
client = Client.from_file("./actguard.json")
```

`ACTGUARD_CONFIG` can be either a base64-encoded JSON blob or a path to a JSON config file.

## Set a USD limit

Stop spending as soon as a run crosses $0.05:

```python
import openai
from actguard import Client
from actguard.exceptions import (
    ActGuardPaymentRequired,
    BudgetExceededError,
    BudgetTransportError,
)

ag = Client.from_env()
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

## Nested budget scopes

You can attach nested scopes to the same run:

```python
with ag.run(user_id="alice"):
    with ag.budget_guard(name="root", usd_limit=0.10) as root:
        ...
        with ag.budget_guard(name="search", usd_limit=0.02) as search:
            ...
```

Root scopes expose root totals. Nested scopes expose local totals by default and also expose `root_tokens_used` / `root_usd_used`.

## Async usage

Both run scopes and budget scopes support `async with`:

```python
import asyncio
import openai
from actguard import Client

async def main():
    ag = Client.from_env()
    oai = openai.AsyncOpenAI()

    async with ag.run(user_id="dave"):
        async with ag.budget_guard(usd_limit=0.10) as guard:
            response = await oai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Hello!"}],
            )
            print(response.choices[0].message.content)

    print(f"Used ${guard.usd_used:.4f}")

asyncio.run(main())
```

## Streaming

Streaming responses are fully supported:

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

For OpenAI chat-completions streams, ActGuard injects `stream_options={"include_usage": true}` so the final chunk includes usage data.

## Run-scoped decorators

`max_attempts` and `idempotent` require an active `client.run(...)` context:

```python
import actguard
from actguard import max_attempts

@max_attempts(calls=2)
def lookup_customer(customer_id: str) -> dict:
    ...

client = actguard.Client.from_env()
with client.run(run_id="req-123"):
    lookup_customer("cus_1")
    lookup_customer("cus_1")
```

## Rate-limit a tool

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

## Circuit-break a tool

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

```python
import actguard
from actguard import idempotent

@idempotent(ttl_s=600)
def create_invoice(user_id: str, amount_cents: int, *, idempotency_key: str) -> str:
    ...

client = actguard.Client.from_env()
with client.run(user_id="alice"):
    invoice_id = create_invoice("alice", 5000, idempotency_key="inv-42")
    same_invoice_id = create_invoice("alice", 5000, idempotency_key="inv-42")
```

## Chain-of-custody: session + prove + enforce

`prove` and `enforce` require an active `actguard.session(...)`:

```python
import actguard

with actguard.session("req-123", {"user_id": "alice"}):
    ...
```

Use `client.run(...)` for `max_attempts` / `idempotent`, and `actguard.session(...)` for `prove` / `enforce`.

## Prove then enforce in one flow

```python
import actguard
from actguard.exceptions import PolicyViolationError

@actguard.prove(kind="order_id", extract="id")
def list_orders(user_id: str) -> list[dict]:
    return [{"id": "o1"}]

@actguard.enforce([actguard.RequireFact("order_id", "order_id")])
def delete_order(order_id: str) -> str:
    return f"deleted:{order_id}"

try:
    with actguard.session("req-9", {"user_id": "alice"}):
        list_orders("alice")
        delete_order("o1")
except PolicyViolationError as e:
    print(e.to_prompt())
```

## Combine guards with `@actguard.tool`

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

client = actguard.Client.from_env()
with client.run():
    search_web("alice", "latest earnings", idempotency_key="req-1")
```

## What's next

- [Core concepts](./concepts.md)
- [Tool guards](./tool-guards.md)
- [Integrations](./integrations/openai.md)
- [API reference](./api-reference.md)
