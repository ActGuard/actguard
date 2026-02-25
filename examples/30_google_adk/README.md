# Google ADK ActGuard Demo

This example runs the same support-triage flow using Google ADK `SequentialAgent` + `LlmAgent` for the summarization stage, while using shared ActGuard-decorated tools for status, incident, and notify.

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
4. Run all 5 stages via ADK `SequentialAgent` (LLM mode) or directly (`--no_llm`):
   - **SummarizeAgent** — classify ticket (service, urgency, severity)
   - **StatusAgent** — check service health
   - **DecisionAgent** — decide whether to create an incident
   - **IncidentAgent** — create incident if needed (idempotent)
   - **NotifyAgent** — notify on-call (rate-limit + max attempts)
5. Print result, guard errors, budget totals

## Install

```bash
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

Add your Gemini API key to `.env`:

```
GOOGLE_API_KEY=your-gemini-api-key-here
```

Or export it directly:

```bash
export GOOGLE_API_KEY="your-gemini-api-key-here"
export ACTGUARD_DEMO_MODEL="gemini-2.5-flash"  # optional
python main.py --mode happy
```

`.env` is auto-loaded if present.
