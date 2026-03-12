from __future__ import annotations

from actguard.reporting import emit_event


def emit_budget_blocked(state) -> None:
    try:
        from actguard.core.budget_context import blocked_scope_metadata

        emit_event(
            "budget",
            "blocked",
            {
                "user_id": state.user_id,
                **blocked_scope_metadata(state),
            },
            severity="error",
            outcome="blocked",
        )
    except Exception:
        pass
