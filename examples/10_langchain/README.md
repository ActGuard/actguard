# LangChain ActGuard Demo

This example shows the support-triage flow with LangChain used for summarization when LLM mode is enabled.

## Available modes (what each one does)

- `happy`: runs summarize -> status -> (incident if urgent+impacted) -> notify once. Usually no guard errors.
- `slow_dependency`: `lookup_status` sleeps longer than its `@timeout`, so you get `ToolTimeoutError`.
- `dependency_down`: `lookup_status` raises dependency failures repeatedly; breaker opens and you get `CircuitOpenError`.
- `loop`: notify is attempted multiple times; first call can pass, then `@rate_limit` blocks and `@max_attempts` eventually blocks too.
- `retry_duplicate`: incident creation is intentionally called twice with the same `idempotency_key`; second call returns the same incident id (idempotent behavior).

## Execution order

1. Parse CLI args
2. Enter `RunContext` (required for `idempotent` and `max_attempts`)
3. Enter `BudgetGuard`
4. Summarize ticket (LangChain + OpenAI if LLM mode, stub if `--no_llm`)
5. Check service status
6. Create incident if urgent + impacted
7. Notify on-call (rate-limited + max attempts)
8. Print result, guard errors, budget totals

## Install

```bash
pip install -e libs/sdk-py
pip install -r examples/10_langchain/requirements.txt
```

## Run without LLM

```bash
python examples/10_langchain/main.py --mode happy --no_llm
python examples/10_langchain/main.py --mode slow_dependency --no_llm
python examples/10_langchain/main.py --mode dependency_down --no_llm
python examples/10_langchain/main.py --mode loop --no_llm
python examples/10_langchain/main.py --mode retry_duplicate --no_llm
```

## Run with LLM

Do not pass `--no_llm`.

```bash
export OPENAI_API_KEY="sk-..."
export ACTGUARD_DEMO_MODEL="gpt-4o-mini"  # optional
python examples/10_langchain/main.py --mode happy
```

`.env` is also supported automatically (repo root or current working directory):

```bash
OPENAI_API_KEY=sk-...
ACTGUARD_DEMO_MODEL=gpt-4o-mini
```
