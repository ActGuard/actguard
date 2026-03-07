# LangGraph ActGuard Demo

This example runs the full flow as a LangGraph pipeline.

## Available modes (what each one does)

- `happy`: runs summarize -> status -> (incident if urgent+impacted) -> notify once. Usually no guard errors.
- `slow_dependency`: `lookup_status` sleeps longer than its `@timeout`, so you get `ToolTimeoutError`.
- `dependency_down`: `lookup_status` raises dependency failures repeatedly; breaker opens and you get `CircuitOpenError`.
- `loop`: notify is attempted multiple times; first call can pass, then `@rate_limit` blocks and `@max_attempts` eventually blocks too.
- `retry_duplicate`: incident creation is intentionally called twice with the same `idempotency_key`; second call returns the same incident id (idempotent behavior).

## Graph order

`summarize -> status -> decision -> incident -> notify`

```mermaid
flowchart LR
  A[summarize] --> B[status]
  B --> C[decision]
  C --> D[incident]
  D --> E[notify]
```

## What each node does

- `summarize`: summarize ticket + extract urgency/service
- `status`: check dependency status (and trigger timeout/circuit modes)
- `decision`: decide whether incident creation is needed
- `incident`: create incident (idempotent; duplicate call in `retry_duplicate`)
- `notify`: notify on-call (rate-limited + max attempts)

## Runtime scopes

The script now uses:

1. `client = actguard.Client.from_env()`
2. `with client.run(...)` for runtime-scoped guards/reporting
3. Optional `with client.budget_guard(usd_limit=...)` when `--usd_limit` is set

## Install

```bash
cd examples/20_langgraph
python -m venv .venv
source .venv/bin/activate
pip install -e ../../libs/sdk-py
pip install -r requirements.txt
```

## Run without LLM

```bash
python main.py --mode happy --no_llm
python main.py --mode slow_dependency --no_llm
python main.py --mode dependency_down --no_llm
python main.py --mode loop --no_llm
python main.py --mode retry_duplicate --no_llm
```

Optional budget scope (reserve/settle-backed):

```bash
python main.py --mode happy --no_llm --usd_limit 0.05
```

`client.budget_guard(...)` requires ActGuard gateway config (for reserve/settle), typically via `ACTGUARD_CONFIG`.

## Run with LLM

Do not pass `--no_llm`.

```bash
export OPENAI_API_KEY="sk-..."
export ACTGUARD_DEMO_MODEL="gpt-4o-mini"  # optional
python main.py --mode happy
```

`.env` is auto-loaded if present.
