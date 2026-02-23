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

Either limit triggers the error — whichever is hit first:

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

## What's next

- [Core Concepts](./concepts.md) — understand how limits and isolation work
- [Integrations](./integrations/openai.md) — provider-specific requirements
- [API Reference](./api-reference.md) — full parameter and exception reference
