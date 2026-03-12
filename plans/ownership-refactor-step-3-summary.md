# Ownership Refactor Step 3 Summary

## Goal
Move provider patch ownership into integrations.

## Changes Made
- Added `IntegrationBootstrap` plus `ensure_patched()` in `actguard.integrations.manager`.
- Replaced direct `patch_all()` usage in `BudgetGuard.__enter__` with `Client.prepare_budget_scope()`.
- Kept `actguard.integrations.patch_all` as a compatibility wrapper.

## Preserved Behavior
- Provider patching is still automatic for budget-guarded execution.
- Provider patching remains idempotent.
- Existing integration-oriented budget tests continued to pass.

## Ownership Locked
- Integrations patch providers.
- `Client` decides when integration setup is triggered.
- `BudgetGuard` no longer owns provider-patching details.

## Constraints For Next Step
- Reporting cleanup must not reintroduce budget-domain behavior into integration bootstrap or provider modules.
