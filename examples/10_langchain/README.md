# LangChain ActGuard Demo

This example shows the support-triage flow using `create_agent` — a single agent with 5 tools. It is conceptually identical to the Google ADK example: define a shared state dict, define 5 tool functions that mutate it, create one agent, invoke it.

## Available modes (what each one does)

- `happy`: runs summarize -> status -> decision -> incident (if urgent+impacted) -> notify once. Usually no guard errors.
- `slow_dependency`: `lookup_status` sleeps longer than its `@timeout`, so you get `ToolTimeoutError`.
- `dependency_down`: `lookup_status` raises dependency failures repeatedly; breaker opens and you get `CircuitOpenError`.
- `loop`: notify is attempted multiple times; first call can pass, then `@rate_limit` blocks and `@max_attempts` eventually blocks too.
- `retry_duplicate`: incident creation is intentionally called twice with the same `idempotency_key`; second call returns the same incident id (idempotent behavior).

## Execution order

1. Parse CLI args
2. Enter `RunContext` (required for `idempotent` and `max_attempts`)
3. Enter `BudgetGuard`
4. Run 5 stages via `create_agent` (LLM mode) or directly (with `--no_llm`):
   - **summarize_tool** — summarize ticket text
   - **status_tool** — check service status (circuit-breaker + timeout)
   - **decision_tool** — decide whether to create an incident
   - **incident_tool** — create incident if needed (idempotent)
   - **notify_tool** — notify on-call (rate-limited + max attempts)
5. Print result, guard errors, budget totals

## Install

```bash
cd examples/10_langchain
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

## Run with LLM

```bash
cd examples/10_langchain
export OPENAI_API_KEY="sk-..."
export ACTGUARD_DEMO_MODEL="gpt-4o-mini"  # optional
python main.py --mode happy
```

`.env` is also supported automatically (repo root or current working directory):

```
OPENAI_API_KEY=sk-...
ACTGUARD_DEMO_MODEL=gpt-4o-mini
```
