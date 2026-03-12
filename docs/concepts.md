# Core concepts

## Runtime model

ActGuard has two distinct runtime scopes:

- `client.run(...)` for run-scoped state such as `max_attempts`, `idempotent`, and runtime event attribution
- `client.budget_guard(...)` for spend tracking and reserve/settle-backed budget enforcement inside an active run

Chain-of-custody uses a separate session scope:

- `actguard.session(...)` for `prove` and `enforce`

## Client and run scope

`Client` is the entrypoint for runtime-scoped behavior.

```python
from actguard import Client

client = Client.from_env()

with client.run(user_id="alice", run_id="req-123") as run:
    print(run.run_id)
```

Run scope stores:

- attempt counters for `max_attempts`
- idempotency records for `idempotent`
- run metadata used by reporting and exceptions

If `max_attempts`, `idempotent`, or `budget_guard` runs without an active run scope, ActGuard raises `MissingRuntimeContextError`.

## Budget scope

`client.budget_guard(...)` creates a client-bound `BudgetGuard` inside an active run:

```python
with client.run(user_id="alice"):
    with client.budget_guard(name="root", usd_limit=0.10) as guard:
        ...
```

### What it tracks

- provider/model attribution
- input, cached-input, and output tokens
- cumulative USD spend
- reserve/settle state for the root scope

### Limits

The current SDK enforces USD budgets. `BudgetExceededError.limit_type` is `"usd"`.

### Root and nested scopes

Nested budget scopes share the same run-level reserve but keep local totals:

```python
with client.run(user_id="alice"):
    with client.budget_guard(name="root", usd_limit=0.10) as root:
        ...
        with client.budget_guard(name="search", usd_limit=0.02) as nested:
            ...
```

- root scopes expose aggregate totals through `tokens_used` / `usd_used`
- nested scopes expose local totals through `tokens_used` / `usd_used`
- both expose `root_tokens_used` / `root_usd_used`

## Budget lifecycle

For a root scope, ActGuard:

1. validates there is an active `client.run(...)`
2. patches supported provider transports
3. reserves budget on enter
4. records usage during model calls
5. settles budget on exit

Missing gateway credentials raise `BudgetTransportError`. A 402 from the budget API raises `ActGuardPaymentRequired`.

## Patching

Budget scopes call the provider patchers on entry. The patching is idempotent and transparent:

- with no active budget scope, patched SDK methods behave like the originals
- while a budget scope is active, usage is captured from non-streaming and streaming responses

See [Integrations](./integrations/openai.md) for provider-specific transport behavior.

## Context isolation

ActGuard stores runtime state in `ContextVar`-backed scopes.

- thread and task isolation prevent unrelated runs from sharing budget or tool state
- run scope isolates attempt counters and idempotency records per run
- session scope isolates verified facts by session id and scope hash

## Chain-of-custody session

`actguard.session(...)` provides the state required by `prove` and `enforce`:

```python
import actguard

with actguard.session("req-123", {"user_id": "u1"}):
    ...
```

Session scope stores:

- verified facts minted by `@prove`
- session id and scope dimensions used for visibility checks

Without an active session, `prove` and `enforce` raise `PolicyViolationError` with code `NO_SESSION`.

## Runtime exception shape

Concrete guard/runtime exceptions live under `actguard.exceptions`.

```python
from actguard.exceptions import BudgetExceededError

try:
    with client.run(user_id="alice"):
        with client.budget_guard(usd_limit=0.01):
            client_llm_call(...)
except BudgetExceededError as e:
    print(e.limit_type)  # "usd"
    print(e.tokens_used)
    print(e.usd_used)
    print(e.usd_limit)
```

Full details live in the [API reference](./api-reference.md).
