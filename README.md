# ActGuard

ActGuard validates agent behavior over time.

## Why use this

Your code validates what is being done.
ActGuard validates whether it should be done, given how we got here.

Traditional checks handle one call at a time. Agent failures often happen across calls: wrong IDs carried between steps, retry loops, and budget drift over a session.

## Static Logic vs Dynamic Workflow Integrity

| Concern | Solve With |
|---|---|
| Is the input valid? | `if/else` |
| Is the caller authorized? | RBAC / Auth |
| Is the amount within limits? | Business logic |
| Did the agent hallucinate the ID? | ActGuard |
| Did required steps happen before this action? | ActGuard |
| Is the agent retrying in a loop? | ActGuard |
| Is the session within cost budget? | ActGuard |

## Primary use cases

### 1) BudgetGuard: keep session cost bounded

```python
from actguard import BudgetGuard, BudgetExceededError
import openai

client = openai.OpenAI()

try:
    with BudgetGuard(user_id="alice", usd_limit=0.05) as guard:
        client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Summarize this ticket thread."}],
        )
except BudgetExceededError:
    # stop or downgrade model/tooling for this session
    pass
```

This protects you from silent cost drift when an agent keeps exploring, retrying, or over-calling models.

### 2) prove/enforce: validate the journey, not just the input

```python
import actguard

@actguard.prove(kind="order_id", extract="id")
def list_orders(user_id: str) -> list[dict]:
    return [{"id": "o1"}]

@actguard.enforce([actguard.RequireFact("order_id", "order_id")])
def cancel_order(order_id: str) -> str:
    return f"cancelled:{order_id}"

with actguard.session("req-123", {"user_id": "alice"}):
    list_orders("alice")
    cancel_order("o1")
```

This blocks write actions unless required facts were proven earlier in the same session and scope.

## Secondary guards (complementary controls)

After budget and workflow-integrity controls, these decorators cover common runtime guardrails:

- `rate_limit`: cap call volume in a time window
- `circuit_breaker`: stop hammering unhealthy dependencies
- `max_attempts`: cap retries/attempts per run
- `timeout`: bound wall-clock execution time
- `idempotent`: deduplicate side-effectful operations
- `tool(...)`: compose multiple guards in one declaration

```python
import actguard
from actguard import RunContext

@actguard.tool(
    idempotent={"ttl_s": 600, "on_duplicate": "return"},
    max_attempts={"calls": 3},
    rate_limit={"max_calls": 10, "period": 60, "scope": "user_id"},
    circuit_breaker={"name": "search_api", "max_fails": 3, "reset_timeout": 60},
    timeout=2.0,
)
def search_web(user_id: str, query: str, *, idempotency_key: str) -> str:
    ...

with RunContext():
    search_web("alice", "latest earnings", idempotency_key="req-1")
```

## Install

```bash
pip install actguard
```

## Python SDK quick links

- [Getting Started](./docs/getting-started.md)
- [Tool Guards](./docs/tool-guards.md)
- [API Reference](./docs/api-reference.md)

## Repository structure (brief)

```
actguard/
├── docs/           # Documentation
├── examples/       # Usage examples
└── libs/
    ├── sdk-py/     # Python SDK
    └── sdk-js/     # JavaScript/Node.js SDK
```

## Development

See `libs/sdk-py/` for Python SDK setup, tests, and lint commands.
