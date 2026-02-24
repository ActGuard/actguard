# Tool Guards

## Overview

Tool Guards are decorators for protecting tool functions (the functions your agent can invoke). v0.1 includes:

- `@actguard.rate_limit` for call-rate control.
- `@actguard.circuit_breaker` for dependency-health protection.
- `@actguard.tool(...)` as a unified decorator that composes both.

Enforcement is local and in-process by default. If configured, ActGuard can also report checks to the gateway for global enforcement visibility.

---

## actguard.configure()

```python
actguard.configure(config: str | None = None) -> None
```

Wires in the ActGuard gateway so `@rate_limit`, `@circuit_breaker`, and `@tool` checks can be reported for global enforcement. Decorators work without any configuration.

### Config fields

| Field | Type | Description |
|---|---|---|
| `agent_id` | `str` | Identifier for this agent instance |
| `gateway_url` | `str \| None` | ActGuard gateway endpoint |
| `api_key` | `str \| None` | API key for the gateway |

### Input formats

**1. JSON file path**

```python
import actguard

actguard.configure("./actguard.json")
```

`actguard.json`:
```json
{
  "agent_id": "my-agent",
  "gateway_url": "https://gateway.actguard.io",
  "api_key": "ag_..."
}
```

**2. Base64-encoded JSON string**

```python
import os
import actguard

actguard.configure(os.environ["ACTGUARD_CONFIG"])
```

**3. `ACTGUARD_CONFIG` env var**

```python
actguard.configure()  # reads ACTGUARD_CONFIG
```

**Clear / reset**

```python
actguard.configure(None)  # clears all config
```

---

## @actguard.rate_limit

```python
actguard.rate_limit(
    *,
    max_calls: int = 10,
    period: float = 60.0,
    scope: str | None = None,
)
```

Decorator that enforces a sliding-window call-rate limit on sync and async functions.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_calls` | `int` | `10` | Maximum calls in the window |
| `period` | `float` | `60.0` | Window length in seconds |
| `scope` | `str \| None` | `None` | Function argument name used as key; `None` means global counter |

### Example

```python
from actguard import rate_limit, RateLimitExceeded

@rate_limit(max_calls=5, period=60, scope="user_id")
def send_email(user_id: str, subject: str) -> str:
    ...

try:
    send_email("alice", "Hello")
except RateLimitExceeded as e:
    print(f"Retry in {e.retry_after:.1f}s")
```

---

## FailureKind and presets

`@circuit_breaker` uses typed `FailureKind` values (no string classification API in v0.1):

- `TRANSPORT`
- `TIMEOUT`
- `OVERLOADED`
- `THROTTLED`
- `AUTH`
- `INVALID`
- `NOT_FOUND`
- `CONFLICT`
- `UNKNOWN`

Preset sets:

- `FAIL_ON_DEFAULT = {TRANSPORT, TIMEOUT, OVERLOADED}`
- `IGNORE_ON_DEFAULT = {INVALID, NOT_FOUND, CONFLICT}`
- `FAIL_ON_STRICT = FAIL_ON_DEFAULT | {AUTH, THROTTLED}`
- `FAIL_ON_INFRA_ONLY = {TRANSPORT, TIMEOUT}`

Presets are set-like and support set operations:

```python
from actguard import FailureKind, FAIL_ON_DEFAULT, IGNORE_ON_DEFAULT

fail_on = FAIL_ON_DEFAULT | {FailureKind.AUTH}
ignore_on = IGNORE_ON_DEFAULT - {FailureKind.CONFLICT}
```

---

## @actguard.circuit_breaker

```python
actguard.circuit_breaker(
    *,
    name: str,
    max_fails: int = 3,
    reset_timeout: float = 60.0,
    fail_on: set[FailureKind] = FAIL_ON_DEFAULT,
    ignore_on: set[FailureKind] = IGNORE_ON_DEFAULT,
)
```

Per-decorator CLOSED/OPEN circuit breaker for sync and async functions.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Dependency name shown in open-circuit errors |
| `max_fails` | `int` | `3` | Number of counted failures before opening |
| `reset_timeout` | `float` | `60.0` | Seconds before calls are allowed again |
| `fail_on` | `set[FailureKind]` | `FAIL_ON_DEFAULT` | Kinds that increment/open |
| `ignore_on` | `set[FailureKind]` | `IGNORE_ON_DEFAULT` | Kinds that do not affect breaker state |

### Default behavior

- Count failures for `TRANSPORT`, `TIMEOUT`, and `OVERLOADED`.
- Ignore `INVALID`, `NOT_FOUND`, and `CONFLICT` for breaker state.
- Short-circuit while OPEN and timeout has not elapsed.
- Raise `CircuitOpenError` when short-circuiting.

### Example: standalone

```python
from actguard import circuit_breaker, CircuitOpenError

@circuit_breaker(name="postgres", max_fails=3, reset_timeout=60)
def write_order(order_id: str) -> None:
    ...

try:
    write_order("ord_123")
except CircuitOpenError as e:
    print(f"{e.dependency_name} unavailable; retry in {e.retry_after:.1f}s")
```

### Example: customize defaults

```python
from actguard import FailureKind, FAIL_ON_DEFAULT, circuit_breaker

@circuit_breaker(
    name="payments_api",
    fail_on=FAIL_ON_DEFAULT | {FailureKind.AUTH},
)
def charge_customer(user_id: str, amount_cents: int) -> None:
    ...
```

---

## @actguard.tool (unified decorator)

```python
actguard.tool(
    *,
    rate_limit: dict | None = None,
    circuit_breaker: dict | None = None,
    idempotency_key: ... ,  # reserved, not yet active
    policy: ... ,           # reserved, not yet active
)
```

Single decorator that composes multiple guards.

### Example: rate limit only

```python
@actguard.tool(rate_limit={"max_calls": 5, "period": 60, "scope": "user_id"})
def send_email(user_id: str, subject: str) -> str:
    ...
```

### Example: circuit breaker only

```python
@actguard.tool(circuit_breaker={"name": "redis", "max_fails": 3, "reset_timeout": 30})
def read_session(session_id: str) -> dict:
    ...
```

### Example: combined

```python
from actguard import FAIL_ON_DEFAULT, FailureKind

@actguard.tool(
    rate_limit={"max_calls": 10, "period": 60, "scope": "user_id"},
    circuit_breaker={
        "name": "search_api",
        "max_fails": 3,
        "reset_timeout": 60,
        "fail_on": FAIL_ON_DEFAULT | {FailureKind.AUTH},
    },
)
def search_web(user_id: str, query: str) -> str:
    ...
```

> **Name collision note:** Many frameworks export their own `@tool`. Prefer `import actguard` and `@actguard.tool(...)`.

---

## Exceptions

### ToolGuardError

```python
class actguard.ToolGuardError(Exception)
```

Base exception for tool guard failures.

### RateLimitExceeded

```python
class actguard.RateLimitExceeded(ToolGuardError)
```

Raised when call rate exceeds `max_calls` in `period`.

| Attribute | Type | Description |
|---|---|---|
| `func_name` | `str` | Decorated function name |
| `scope_value` | `str \| None` | Runtime scope value (or global scope) |
| `max_calls` | `int` | Configured call limit |
| `period` | `float` | Configured window |
| `retry_after` | `float` | Seconds until next call is safe |

### CircuitOpenError

```python
class actguard.CircuitOpenError(ToolGuardError)
```

Raised when a breaker is OPEN and call is short-circuited.

| Attribute | Type | Description |
|---|---|---|
| `dependency_name` | `str` | Breaker dependency name |
| `reset_at` | `float` | Epoch seconds when calls may resume |
| `retry_after` | `float` | Seconds remaining until reset |

---

## Stacking order

Keep framework decorators outermost and actguard decorators innermost.

```python
# CORRECT
@framework_tool
@actguard.rate_limit(max_calls=5, period=60, scope="user_id")
@actguard.circuit_breaker(name="mail_api")
def send_email(user_id: str, subject: str) -> str:
    ...

# WRONG
@actguard.circuit_breaker(name="mail_api")
@framework_tool
def send_email(...):
    ...
```

---

## Framework integrations

Pattern is consistent: framework decorator outermost, actguard innermost.

### LangChain / LangGraph

Circuit breaker only:

```python
from langchain_core.tools import tool
import actguard

@tool
@actguard.circuit_breaker(name="crm_api")
def fetch_customer(user_id: str) -> dict:
    ...
```

Combined:

```python
@tool
@actguard.rate_limit(max_calls=10, period=60, scope="user_id")
@actguard.circuit_breaker(name="crm_api")
def fetch_customer(user_id: str) -> dict:
    ...
```

### Pydantic AI

Circuit breaker only:

```python
from pydantic_ai import Agent
import actguard

agent = Agent("openai:gpt-4o")

@agent.tool
@actguard.circuit_breaker(name="mailer")
async def send_email(ctx, user_id: str, subject: str) -> str:
    ...
```

Combined:

```python
@agent.tool
@actguard.rate_limit(max_calls=10, period=60)
@actguard.circuit_breaker(name="mailer")
async def send_email(ctx, user_id: str, subject: str) -> str:
    ...
```

### CrewAI

Circuit breaker only:

```python
from crewai.tools import tool
import actguard

@tool("Send Email Tool")
@actguard.circuit_breaker(name="smtp")
def send_email(user_id: str, subject: str) -> str:
    ...
```

Combined:

```python
@tool("Send Email Tool")
@actguard.rate_limit(max_calls=5, period=60, scope="user_id")
@actguard.circuit_breaker(name="smtp")
def send_email(user_id: str, subject: str) -> str:
    ...
```

### Google ADK (Python)

Circuit breaker only:

```python
from google.adk.agents import Agent
import actguard

@actguard.circuit_breaker(name="inventory_rpc")
def get_stock(sku: str) -> int:
    ...

agent = Agent(name="my-agent", model="gemini-2.0-flash", tools=[get_stock])
```

Combined:

```python
@actguard.rate_limit(max_calls=20, period=60, scope="user_id")
@actguard.circuit_breaker(name="inventory_rpc")
def get_stock(user_id: str, sku: str) -> int:
    ...
```

### OpenAI Agents SDK

Circuit breaker only:

```python
from agents import function_tool
import actguard

@function_tool
@actguard.circuit_breaker(name="ticketing_api")
def create_ticket(user_id: str, title: str) -> str:
    ...
```

Combined:

```python
@function_tool
@actguard.rate_limit(max_calls=10, period=60, scope="user_id")
@actguard.circuit_breaker(name="ticketing_api")
def create_ticket(user_id: str, title: str) -> str:
    ...
```

### smolagents

Circuit breaker only:

```python
from smolagents import tool, CodeAgent
import actguard

@tool
@actguard.circuit_breaker(name="calendar_api")
def schedule(user_id: str, slot: str) -> str:
    """Schedule a slot for a user."""
    ...

agent = CodeAgent(tools=[schedule], model=...)
```

Combined:

```python
@tool
@actguard.rate_limit(max_calls=5, period=60, scope="user_id")
@actguard.circuit_breaker(name="calendar_api")
def schedule(user_id: str, slot: str) -> str:
    """Schedule a slot for a user."""
    ...
```

### AutoGen

Circuit breaker only:

```python
from autogen import ConversableAgent
import actguard

@actguard.circuit_breaker(name="warehouse_api")
def reserve_item(user_id: str, sku: str) -> bool:
    ...

agent = ConversableAgent(name="agent", llm_config={...})
agent.register_for_llm(description="Reserve warehouse item")(reserve_item)
agent.register_for_execution()(reserve_item)
```

Combined:

```python
@actguard.rate_limit(max_calls=5, period=60)
@actguard.circuit_breaker(name="warehouse_api")
def reserve_item(user_id: str, sku: str) -> bool:
    ...
```

### Agno (formerly phidata)

Circuit breaker only:

```python
from agno.agent import Agent
import actguard

@actguard.circuit_breaker(name="pricing_service")
def quote(user_id: str, sku: str) -> dict:
    ...

agent = Agent(tools=[quote], model=...)
```

Combined:

```python
@actguard.rate_limit(max_calls=10, period=60, scope="user_id")
@actguard.circuit_breaker(name="pricing_service")
def quote(user_id: str, sku: str) -> dict:
    ...
```
