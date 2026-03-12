# Anthropic Integration

actguard patches `anthropic.resources.messages.Messages.create` and its async counterpart, intercepting every call to the Messages API.

## Requirements

| Requirement | Version |
|-------------|---------|
| `anthropic` SDK | any recent release |
| Python | ≥ 3.9 |

```bash
pip install anthropic
```

## What gets patched

```
anthropic.resources.messages.Messages.create       → actguard wrapper (sync)
anthropic.resources.messages.AsyncMessages.create  → actguard wrapper (async)
```

## Non-streaming

```python
import anthropic
from actguard import Client

ag = Client.from_env()
client = anthropic.Anthropic()

with ag.run(user_id="alice"):
    with ag.budget_guard(usd_limit=0.10) as guard:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "Hello!"}],
        )
        print(message.content[0].text)

print(f"${guard.usd_used:.6f}  ({guard.tokens_used} tokens)")
```

## Streaming

actguard reads `message_start` (input tokens) and `message_delta` (output tokens) SSE events and records usage after the stream is exhausted.

```python
with ag.run(user_id="alice"):
    with ag.budget_guard(usd_limit=0.10) as guard:
        with client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": "Write a haiku."}],
            stream=True,
        ) as stream:
            for event in stream:
                if hasattr(event, "delta") and hasattr(event.delta, "text"):
                    print(event.delta.text, end="", flush=True)

print(f"\n${guard.usd_used:.6f}")
```

## Async client

```python
import asyncio
import anthropic
from actguard import Client

ag = Client.from_env()
client = anthropic.AsyncAnthropic()

async def main():
    async with ag.run(user_id="alice"):
        async with ag.budget_guard(usd_limit=0.10) as guard:
            message = await client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1024,
                messages=[{"role": "user", "content": "Hello!"}],
            )
    print(f"${guard.usd_used:.6f}")

asyncio.run(main())
```
