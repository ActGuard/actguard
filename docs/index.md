# Overview

**actguard** is a Python SDK for runtime-scoped budget control and tool-guard enforcement in LLM agents.

It patches supported provider SDKs at the transport layer while a budget scope is active, tracks spend in real time, and raises guard exceptions when a run exceeds its configured budget or violates tool policy.

## Installation

```bash
pip install actguard
```

## Quickstart

```python
import openai
from actguard import Client
from actguard.exceptions import BudgetExceededError

ag = Client.from_env()
oai = openai.OpenAI()

try:
    with ag.run(user_id="alice"):
        with ag.budget_guard(usd_limit=0.05) as guard:
            response = oai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Hello!"}],
            )
            print(response.choices[0].message.content)
except BudgetExceededError:
    print("Budget hit.")

print(f"Used ${guard.usd_used:.4f}")
```

For reserve/settle-backed budget scopes, configure the client with `ACTGUARD_CONFIG` or `Client.from_file(...)`.

## Key features

- **USD budget scopes** with reserve/settle accounting
- **Run-scoped tool state** via `client.run(...)` for `max_attempts` and `idempotent`
- **Chain-of-custody sessions** via `actguard.session(...)` for `prove` and `enforce`
- **Streaming support** across supported provider SDKs
- **Async support** for run scopes, budget scopes, and sessions
- **Multi-provider integrations** for OpenAI, Anthropic, and Google SDKs
- **Concrete exception taxonomy** under `actguard.exceptions`

## How it works

```text
your code
  └── client.run(...)
        └── client.budget_guard(...)
              ├── patch supported provider transports
              ├── reserve budget on enter
              ├── collect usage from responses / streams
              ├── accumulate usd_used and tokens_used
              └── settle budget on exit
```

## Next steps

- [Getting started](./getting-started.md)
- [Core concepts](./concepts.md)
- [Tool guards](./tool-guards.md)
- [Integrations](./integrations/openai.md)
- [API reference](./api-reference.md)
