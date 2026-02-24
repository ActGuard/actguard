# Getting Started

## Requirements

- Python 3.9+
- At least one supported LLM SDK installed (see [Integrations](./integrations/openai.md))

## Install actguard

```bash
pip install actguard
```

## Set a USD limit

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

## Set a token limit

```python
with BudgetGuard(user_id="bob", token_limit=1_000) as guard:
    ...
```

## Set both limits

Either limit triggers the error, whichever is hit first:

```python
with BudgetGuard(user_id="carol", token_limit=5_000, usd_limit=0.10) as guard:
    ...
```

## Async usage

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

## Streaming

Streaming responses are fully supported. actguard wraps the iterator transparently and captures the usage chunk emitted at the end of the stream:

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

> **Note:** For streaming chat completions, actguard automatically injects
> `stream_options={"include_usage": true}` into the request so the OpenAI SDK
> returns a usage chunk. This is harmless to your code.

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

## Combine rate limit + circuit breaker

Use stacked decorators:

```python
import actguard

@actguard.rate_limit(max_calls=10, period=60, scope="user_id")
@actguard.circuit_breaker(name="search_api", max_fails=3, reset_timeout=60)
def search_web(user_id: str, query: str) -> str:
    ...
```

Or use the unified decorator:

```python
import actguard

@actguard.tool(
    rate_limit={"max_calls": 10, "period": 60, "scope": "user_id"},
    circuit_breaker={"name": "search_api", "max_fails": 3, "reset_timeout": 60},
)
def search_web(user_id: str, query: str) -> str:
    ...
```

## Which guard should I use?

- Use `rate_limit` to cap request volume per window.
- Use `circuit_breaker` to stop hammering unhealthy dependencies.
- Use both when you need both caller fairness and dependency protection.

## What's next

- [Core Concepts](./concepts.md) - understand how limits and isolation work
- [Tool Guards](./tool-guards.md) - rate limiting, circuit breaker, and framework integrations
- [Integrations](./integrations/openai.md) - provider-specific requirements
- [API Reference](./api-reference.md) - full parameter and exception reference
