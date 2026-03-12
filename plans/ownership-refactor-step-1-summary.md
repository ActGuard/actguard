# Ownership Refactor Step 1 Summary

## Goal
Move runtime lifecycle behavior next to runtime state while preserving `Client.run(...)`.

## Changes Made
- Added `actguard.core.runtime.ClientRunContext`.
- Moved run-context enter/exit behavior out of `actguard.client`.
- Kept `Client.run(...)` as the unchanged public entrypoint.

## Preserved Behavior
- Nested runtime contexts still raise `NestedRuntimeContextError`.
- Run start/end events are still emitted from the same lifecycle boundaries.
- Existing tests around run state continued to pass.

## Ownership Locked
- Runtime lifecycle logic belongs with runtime modules, not inline inside `Client`.
- `Client` remains a facade over runtime entry.

## Constraints For Next Step
- Do not move run-state access or lifecycle bookkeeping back into `Client`.
- Preserve the current `Client.run(...)` signature and behavior.
