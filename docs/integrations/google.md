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
from actguard import BudgetGuard

genai.configure(api_key="YOUR_API_KEY")
model = genai.GenerativeModel("gemini-1.5-pro")

with BudgetGuard(user_id="alice", usd_limit=0.10) as guard:
    response = model.generate_content("Explain quantum computing.")
    print(response.text)

print(f"${guard.usd_used:.6f}  ({guard.tokens_used} tokens)")
```

## Streaming

For streaming, actguard reads `usage_metadata` from the first chunk that carries it:

```python
with BudgetGuard(user_id="alice", usd_limit=0.10) as guard:
    for chunk in model.generate_content("Write a poem.", stream=True):
        print(chunk.text, end="", flush=True)

print(f"\n${guard.usd_used:.6f}")
```

## Async client

```python
import asyncio
import google.generativeai as genai
from actguard import BudgetGuard

genai.configure(api_key="YOUR_API_KEY")
model = genai.GenerativeModel("gemini-1.5-pro")

async def main():
    async with BudgetGuard(user_id="alice", usd_limit=0.10) as guard:
        response = await model.generate_content_async("Hello!")
    print(f"${guard.usd_used:.6f}")

asyncio.run(main())
```

## Model name normalisation

The Google SDK prefixes model names with `models/` (e.g. `models/gemini-1.5-pro`). actguard strips this prefix before looking up the pricing table, so `gemini-1.5-pro` is the key used for cost calculation.
