# Google Generative AI Integration

actguard patches `google.generativeai.GenerativeModel.generate_content` and its async counterpart.

## Requirements

| Requirement | Version |
|-------------|---------|
| `google-generativeai` SDK | any recent release |
| Python | ≥ 3.9 |

```bash
pip install google-generativeai
```

## What gets patched

```
google.generativeai.GenerativeModel.generate_content        → actguard wrapper (sync)
google.generativeai.GenerativeModel.generate_content_async  → actguard wrapper (async)
```

## Non-streaming

```python
import google.generativeai as genai
from actguard import Client

genai.configure(api_key="YOUR_API_KEY")
model = genai.GenerativeModel("gemini-1.5-pro")
ag = Client.from_env()

with ag.run(user_id="alice"):
    with ag.budget_guard(token_limit=100_000) as guard:
        response = model.generate_content("Explain quantum computing.")
        print(response.text)

print(f"{guard.tokens_used} tokens")
```

## Streaming

For streaming, actguard reads `usage_metadata` from the first chunk that carries it:

```python
with ag.run(user_id="alice"):
    with ag.budget_guard(token_limit=100_000) as guard:
        for chunk in model.generate_content("Write a poem.", stream=True):
            print(chunk.text, end="", flush=True)

print(f"\n{guard.tokens_used} tokens")
```

## Async client

```python
import asyncio
import google.generativeai as genai
from actguard import Client

genai.configure(api_key="YOUR_API_KEY")
model = genai.GenerativeModel("gemini-1.5-pro")
ag = Client.from_env()

async def main():
    async with ag.run(user_id="alice"):
        async with ag.budget_guard(token_limit=100_000) as guard:
            response = await model.generate_content_async("Hello!")
    print(f"{guard.tokens_used} tokens")

asyncio.run(main())
```

## Model name normalisation

The Google SDK prefixes model names with `models/` (e.g. `models/gemini-1.5-pro`). actguard strips this prefix before recording provider/model attribution, so `gemini-1.5-pro` is the normalized model id stored in runtime usage data.
