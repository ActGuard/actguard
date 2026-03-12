# Ownership Refactor Step 2 Summary

## Goal
Narrow `Client` to orchestration and collaborator wiring.

## Changes Made
- Added `actguard.transport.budget_api.BudgetTransport`.
- `Client.reserve_budget(...)` and `Client.settle_budget(...)` now delegate to the transport collaborator.
- `Client` now owns integration bootstrap and budget transport wiring.

## Preserved Behavior
- Public `Client` methods and signatures are unchanged.
- Reserve/settle request payloads and error behavior remain compatible with the existing tests.

## Ownership Locked
- `Client` constructs and coordinates collaborators.
- Transport details do not live inline inside `Client`.

## Constraints For Next Step
- Automatic provider patching should be triggered through `Client`, not directly inside `BudgetGuard`.
- Keep `Client` as the single orchestration entrypoint.
