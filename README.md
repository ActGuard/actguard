# ActGuard

ActGuard validates agent behavior over time.

## Why use this

Your code validates what is being done.
ActGuard validates whether it should be done, given how the agent got there.

Traditional checks handle one call at a time. Agent failures often happen across calls: wrong IDs carried between steps, retry loops, and budget drift over a run.

## Static logic vs dynamic workflow integrity

| Concern | Solve with |
|---|---|
| Is the input valid? | `if/else` |
| Is the caller authorized? | RBAC / Auth |
| Is the amount within limits? | Business logic |
| Did the agent hallucinate the ID? | ActGuard |
| Did required steps happen before this action? | ActGuard |
| Is the agent retrying in a loop? | ActGuard |
| Is the run within cost budget? | ActGuard |

## Primary use cases

### 1) Bound spend with `Client.run(...)` + `client.budget_guard(...)`

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

try:
    with ag.run(user_id="alice"):
        with ag.budget_guard(usd_limit=0.05) as guard:
            oai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": "Summarize this ticket thread."}],
            )
except BudgetExceededError:
    pass
except ActGuardPaymentRequired:
    pass
except BudgetTransportError:
    pass
```

This protects you from silent cost drift when an agent keeps exploring, retrying, or over-calling models. Budget scopes reserve on entry and settle on exit, so they require a configured `Client` with gateway credentials.

### 2) Prove/enforce: validate the journey, not just the input

```python
import actguard
from actguard.exceptions import PolicyViolationError

@actguard.prove(kind="order_id", extract="id")
def list_orders(user_id: str) -> list[dict]:
    return [{"id": "o1"}]

@actguard.enforce([actguard.RequireFact("order_id", "order_id")])
def cancel_order(order_id: str) -> str:
    return f"cancelled:{order_id}"

try:
    with actguard.session("req-123", {"user_id": "alice"}):
        list_orders("alice")
        cancel_order("o1")
except PolicyViolationError as e:
    hint_for_llm = e.to_prompt()
```

This blocks actions that look valid by input but are invalid for the session journey.

## Secondary guards

After budget and workflow-integrity controls, these decorators cover common runtime guardrails:

- `rate_limit`: cap call volume in a time window
- `circuit_breaker`: stop hammering unhealthy dependencies
- `max_attempts`: cap retries per run
- `timeout`: bound wall-clock execution time
- `idempotent`: deduplicate side-effectful operations
- `tool(...)`: compose multiple guards in one declaration

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
with client.run(user_id="alice"):
    search_web("alice", "latest earnings", idempotency_key="req-1")
```

## Install

```bash
pip install actguard
```

## Python SDK quick links

- [Getting started](./docs/getting-started.md)
- [Tool guards](./docs/tool-guards.md)
- [API reference](./docs/api-reference.md)

## Repository structure

```text
actguard/
├── docs/
├── examples/
└── libs/
    ├── sdk-py/
    └── sdk-js/
```

## Development

See `libs/sdk-py/` for Python SDK setup, tests, and lint commands.
