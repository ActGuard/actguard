# Ownership Refactor Master

## Objective
Keep the external package shape unchanged while clarifying internal ownership:
- `Client` orchestrates collaborators and entrypoints.
- Runtime modules own run/budget lifecycle state.
- Integrations own provider patching.
- Reporting/observability emit events without budget-domain behavior.

## Status
- Step 1 Runtime Ownership: completed
- Step 2 Client Orchestration: completed
- Step 3 Integration Bootstrap: completed
- Step 4 Reporting Boundary Cleanup: completed

## Locked Ownership Rules
- Public imports remain centered on `actguard.Client`, `BudgetGuard`, decorators, `session`, and `reporting`.
- `Client` now wires runtime context, budget transport, and integration bootstrap.
- Runtime lifecycle lives in `actguard.core.runtime`.
- Budget reserve/settle HTTP transport lives in `actguard.transport.budget_api`.
- Automatic provider patching is triggered through `Client.prepare_budget_scope()` and implemented in `actguard.integrations.manager`.
- Budget-blocked event emission lives in `actguard.budget_events`, not in reporting.
- `actguard.reporting` is now a facade over observability helpers.
- Version is sourced from `actguard/_version.py` and package metadata is dynamic.

## Current Internal Module Map
- Runtime: `actguard.core.run_context`, `actguard.core.budget_context`, `actguard.core.runtime`
- Client orchestration: `actguard.client`
- Budget transport: `actguard.transport.budget_api`
- Integrations bootstrap: `actguard.integrations.manager`
- Generic observability: `actguard.observability.events`, `actguard.observability.violations`
- Budget-domain event helper: `actguard.budget_events`

## Deferred Cleanup
- The broader repo still has unrelated Ruff line-length/import issues outside the refactored file set.
- Compatibility shims such as `actguard.core.state.get_current_state()` remain in place.

## Verification
- `uv run --project libs/sdk-py --extra dev ruff check ...` on the refactored files
- `uv run --project libs/sdk-py --extra dev python -m pytest libs/sdk-py/tests/test_client.py libs/sdk-py/tests/test_budget_guard.py libs/sdk-py/tests/test_event_emission.py libs/sdk-py/tests/test_reporting_contract.py`

## Latest Step Summary
- `plans/ownership-refactor-step-4-summary.md`
