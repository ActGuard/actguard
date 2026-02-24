# Google ADK ActGuard Demo

This example runs the same support-triage flow with Google ADK environment compatibility, while using the shared ActGuard-decorated tools.

## Available modes (what each one does)

- `happy`: runs summarize -> status -> (incident if urgent+impacted) -> notify once. Usually no guard errors.
- `slow_dependency`: `lookup_status` sleeps longer than its `@timeout`, so you get `ToolTimeoutError`.
- `dependency_down`: `lookup_status` raises dependency failures repeatedly; breaker opens and you get `CircuitOpenError`.
- `loop`: notify is attempted multiple times; first call can pass, then `@rate_limit` blocks and `@max_attempts` eventually blocks too.
- `retry_duplicate`: incident creation is intentionally called twice with the same `idempotency_key`; second call returns the same incident id (idempotent behavior).

## Execution order

1. Parse CLI args
2. Enter `RunContext`
3. Enter `BudgetGuard`
4. Summarize ticket (LLM or stub)
5. Check service status
6. Create incident (idempotent)
7. Notify on-call (rate-limit + max attempts)
8. Print result, guard errors, budget totals

## Install

```bash
pip install -e libs/sdk-py
pip install -r examples/30_google_adk/requirements.txt
```

## Run without LLM

```bash
python examples/30_google_adk/main.py --mode happy --no_llm
python examples/30_google_adk/main.py --mode slow_dependency --no_llm
python examples/30_google_adk/main.py --mode dependency_down --no_llm
python examples/30_google_adk/main.py --mode loop --no_llm
python examples/30_google_adk/main.py --mode retry_duplicate --no_llm
```

## Run with LLM

Do not pass `--no_llm`.

```bash
export OPENAI_API_KEY="sk-..."
export ACTGUARD_DEMO_MODEL="gpt-4o-mini"  # optional
python examples/30_google_adk/main.py --mode happy
```

`.env` is auto-loaded if present.
