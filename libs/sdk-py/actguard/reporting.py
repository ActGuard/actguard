from __future__ import annotations

from typing import List, Optional


def emit_event(
    category: str,
    name: str,
    payload: dict,
    *,
    severity: str = "",
    outcome: str = "",
    evidence: Optional[List] = None,
    user_id: str = "",
    run_id: str = "",
    trace_id: str = "",
    span_id: str = "",
) -> None:
    """Emit a structured event to the ActGuard gateway."""
    from actguard._config import get_config
    from actguard.events.client import get_client

    client = get_client()
    if client is None:
        return

    config = get_config()
    if config is None:
        return

    from actguard.events.catalog import SIGNIFICANT, VERBOSE

    event_key = f"{category}.{name}"
    mode = config.event_mode
    if mode == "off":
        return
    if mode == "significant" and event_key not in SIGNIFICANT:
        return
    if mode == "verbose" and event_key not in VERBOSE:
        return

    run_id, user_id = _context_ids(run_id, user_id)

    from actguard.events.envelope import Envelope

    envelope = Envelope(
        agent_id=config.agent_id,
        user_id=user_id,
        run_id=run_id,
        trace_id=trace_id,
        span_id=span_id,
        category=category,
        name=name,
        severity=severity,
        outcome=outcome,
        payload=payload,
        evidence=evidence or [],
    )
    client.enqueue(envelope)


def emit_violation(error: Exception, **context_overrides) -> None:
    """Emit a structured violation event for an ActGuardViolation error."""
    from actguard._config import get_config
    from actguard.events.client import get_client

    client = get_client()
    if client is None:
        return

    config = get_config()
    if config is None:
        return

    from actguard.events.envelope import ActGuardContextEvidenceProvider
    from actguard.exceptions import ActGuardViolation
    from actguard.plugins import get_plugins

    if not isinstance(error, ActGuardViolation):
        return

    # Collect evidence
    ev_list = list(error.evidence())
    try:
        ctx_ev = ActGuardContextEvidenceProvider().current()
        ev_list.extend(ctx_ev)
    except Exception:
        pass

    for plugin in get_plugins():
        try:
            for provider in plugin.evidence_providers():
                ev_list.extend(provider.current())
        except Exception:
            pass

    # Derive category/name from error.code
    code = error.code or ""
    if "." in code:
        category, name = code.split(".", 1)
    else:
        category, name = "unknown", code

    err_payload = {}
    try:
        err_payload = error.payload()
    except Exception:
        pass

    emit_event(
        category,
        name,
        err_payload,
        severity=error.severity,
        outcome=error.outcome,
        evidence=ev_list,
        **context_overrides,
    )


def _emit_budget_check(state) -> None:
    try:
        emit_event("budget", "check", {
            "user_id": state.user_id,
            "tokens_used": state.tokens_used,
            "usd_used": state.usd_used,
        })
    except Exception:
        pass


def _emit_budget_consumed(state, model: str, input_tokens: int, output_tokens: int) -> None:
    try:
        emit_event("budget", "consumed", {
            "user_id": state.user_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tokens_used": state.tokens_used,
            "usd_used": state.usd_used,
        })
    except Exception:
        pass


def _emit_budget_blocked(state) -> None:
    try:
        emit_event("budget", "blocked", {
            "user_id": state.user_id,
            "tokens_used": state.tokens_used,
            "usd_used": state.usd_used,
            "token_limit": state.token_limit,
            "usd_limit": state.usd_limit,
        }, severity="error", outcome="blocked")
    except Exception:
        pass


def _context_ids(run_id: str, user_id: str) -> tuple:
    """Fill run_id and user_id from active RunState if not already provided."""
    if run_id and user_id:
        return run_id, user_id
    try:
        from actguard.core.run_context import get_run_state

        state = get_run_state()
        if state is not None:
            if not run_id:
                run_id = state.run_id
            if not user_id:
                user_id = getattr(state, "user_id", "")
    except Exception:
        pass
    return run_id, user_id
