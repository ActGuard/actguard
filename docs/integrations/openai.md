# OpenAI Integration

actguard patches `openai.SyncAPIClient.request` and `openai.AsyncAPIClient.request` — private transport-layer methods that every high-level OpenAI API call passes through.

## Requirements

| Requirement | Version |
|-------------|---------|
| `openai` SDK | **≥ 1.76.0** |
| Python | ≥ 3.9 |

> **Why 1.76.0?** The `request()` signature changed across SDK versions. Prior to
> 1.76.0 it included a `remaining_retries` parameter that our replacement does
> not accept, creating a potential `TypeError`. Version 1.76.0 removed that
> parameter, making the signature an exact match for the patched version.
> Installing an older SDK will trigger a `UserWarning` at runtime.

Install or upgrade:

```bash
pip install "openai>=1.76.0"
```

## What gets patched

```
openai._base_client.SyncAPIClient.request   → actguard wrapper (sync)
openai._base_client.AsyncAPIClient.request  → actguard wrapper (async)
```

Every OpenAI API surface (Chat Completions, Responses API, Embeddings, etc.) routes through these methods, so all calls are automatically tracked.

## Chat completions (non-streaming)

```python
import openai
from actguard import Client

ag = Client.from_env()
oai = openai.OpenAI()

with ag.run(user_id="alice"):
    with ag.budget_guard(usd_limit=0.10) as guard:
        response = oai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Explain async/await in Python."}],
        )
        print(response.choices[0].message.content)

print(f"${guard.usd_used:.6f}  ({guard.tokens_used} tokens)")
```

## Chat completions (streaming)

For streaming, actguard automatically injects `stream_options={"include_usage": true}` into chat completion requests so the final chunk includes usage data. This injection only applies to `/chat/completions` endpoints and is harmless to existing code.

```python
with ag.run(user_id="alice"):
    with ag.budget_guard(usd_limit=0.10) as guard:
        stream = oai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Write a haiku."}],
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            print(delta, end="", flush=True)

print(f"\n${guard.usd_used:.6f}")
```

## Async client

```python
import asyncio
import openai
from actguard import Client

ag = Client.from_env()
oai = openai.AsyncOpenAI()

async def main():
    async with ag.run(user_id="alice"):
        async with ag.budget_guard(usd_limit=0.10) as guard:
            response = await oai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Hello!"}],
            )
    print(f"${guard.usd_used:.6f}")

asyncio.run(main())
```

## Responses API

The Responses API is also tracked. Usage is read from the `response.completed` event chunk when streaming, or from `response.usage` for non-streaming calls.

## Version warning

If you have an older OpenAI SDK installed, actguard emits a warning when patching runs (i.e., the first time you enter a budget scope):

```
UserWarning: actguard requires openai>=1.76.0; detected 1.50.0.
Budget tracking may fail with this SDK version.
```

Upgrade to silence the warning and ensure correct behaviour:

```bash
pip install "openai>=1.76.0"
```
