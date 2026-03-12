# Ownership Refactor Step 4 Summary

## Goal
Keep reporting/observability generic and move budget-domain event behavior out of it.

## Changes Made
- Added `actguard.observability.events` and `actguard.observability.violations`.
- Reduced `actguard.reporting` to a stable facade over the observability helpers.
- Added `actguard.budget_events.emit_budget_blocked(...)`.
- Updated provider integrations to call the budget-domain helper instead of a reporting-internal budget helper.
- Added `actguard._version` and switched package metadata to a single version source.

## Preserved Behavior
- `emit_event`, `emit_usage_event`, and `emit_violation` remain importable from `actguard.reporting`.
- Budget-blocked and usage event semantics remain compatible with the existing tests.
- Public package shape remains unchanged.

## Final Ownership Map
- `Client`: orchestration and collaborator wiring
- Runtime modules: context/state and lifecycle
- Integrations: provider patching
- Observability: generic event emission and violation reporting
- Budget domain: budget-blocked event helper

## Residual Notes
- Compatibility shims remain where they already existed.
- The repo still has unrelated lint backlog outside the refactored files.
