# Shared Demo Layer (`examples/00_shared`)

This folder contains only the shared pieces used by all three framework demos.

## What this demo does

Each framework runs the same support-triage flow:

1. `summarize_ticket(...)`
2. `lookup_status(...)`
3. if urgent + impacted: `create_incident(...)`
4. `notify_oncall(...)`
5. print final result + guard errors + budget usage

## Shared files

- `tools.py`: all shared tools and ActGuard decorators
- `modes.py`: deterministic mode behavior (`happy`, `slow_dependency`, `dependency_down`, `loop`, `retry_duplicate`)

## Guard mapping by mode

- `happy`: normal success path
- `slow_dependency`: `ToolTimeoutError` from `lookup_status`
- `dependency_down`: `CircuitOpenError` after repeated dependency failures
- `loop`: `RateLimitExceeded` and `MaxAttemptsExceeded` during repeated notify attempts
- `retry_duplicate`: second `create_incident` call returns same id via idempotency key

## LLM configuration

The demo reads these env vars:

- `OPENAI_API_KEY` (required for LLM mode)
- `ACTGUARD_DEMO_MODEL` (optional, default `gpt-4o-mini`)

If `.env` exists, the scripts auto-load `OPENAI_API_KEY` and `ACTGUARD_DEMO_MODEL` from it.

Example `.env`:

```bash
OPENAI_API_KEY=sk-...
ACTGUARD_DEMO_MODEL=gpt-4o-mini
```

If no key is available, summarization falls back to deterministic stub behavior.
