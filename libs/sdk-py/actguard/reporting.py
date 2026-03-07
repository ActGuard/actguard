from __future__ import annotations

from typing import Any, List, Optional, Tuple


def _runtime_event_client_and_config() -> Tuple[object, object]:
    """Resolve event client/config strictly from active run state."""
    try:
        from actguard.core.run_context import get_run_state

        state = get_run_state()
        if state is not None:
            if state.client is None:
                return None, None
            return state.client.event_client, state.client.reporting_config
    except Exception:
        pass

    return None, None


def emit_event(
    category: str,
    name: str,
    payload: dict,
    *,
    severity: str = "",
    outcome: str = "",
    evidence: Optional[List] = None,
    user_id: Optional[str] = None,
    run_id: str = "",
    trace_id: str = "",
    span_id: str = "",
    model: Optional[str] = None,
    usd_micros: Optional[int] = None,
    input_tokens: Optional[int] = None,
    cached_input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> None:
    """Emit a structured event to the ActGuard gateway."""
    client, config = _runtime_event_client_and_config()
    if client is None:
        return

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
    if not run_id:
        return

    from actguard.events.envelope import Envelope

    (
        model,
        usd_micros,
        input_tokens,
        cached_input_tokens,
        output_tokens,
    ) = _resolve_usage_fields(
        payload=payload,
        model=model,
        usd_micros=usd_micros,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
    )

    envelope = Envelope(
        user_id=user_id,
        run_id=run_id,
        trace_id=trace_id,
        span_id=span_id,
        category=category,
        name=name,
        severity=severity,
        outcome=outcome,
        model=model,
        usd_micros=usd_micros,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        payload=payload,
        evidence=evidence or [],
    )
    client.enqueue(envelope)


def emit_violation(error: Exception, **context_overrides) -> None:
    """Emit a structured violation event for an ActGuardViolation error."""
    client, config = _runtime_event_client_and_config()
    if client is None:
        return

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


def _emit_budget_consumed(
    state,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> None:
    try:
        emit_event(
            "budget",
            "consumed",
            {
                "user_id": state.user_id,
                "model": model,
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
                "tokens_used": state.tokens_used,
                "usd_used": state.usd_used,
            },
            model=model or None,
            usd_micros=int(round((state.usd_used or 0.0) * 1_000_000)),
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
        )
    except Exception:
        pass


def _emit_budget_blocked(state) -> None:
    try:
        emit_event("budget", "blocked", {
            "user_id": state.user_id,
            "tokens_used": state.tokens_used,
            "usd_used": state.usd_used,
            "usd_limit": state.usd_limit,
        }, severity="error", outcome="blocked")
    except Exception:
        pass


def _context_ids(run_id: str, user_id: Optional[str]) -> tuple[str, Optional[str]]:
    """Fill run_id and user_id from active RunState when available."""
    if user_id == "":
        user_id = None

    if run_id and user_id is not None:
        return run_id, user_id
    try:
        from actguard.core.run_context import get_run_state

        state = get_run_state()
        if state is not None:
            if not run_id:
                run_id = state.run_id
            if user_id is None:
                user_id = state.user_id
                if user_id == "":
                    user_id = None
    except Exception:
        pass

    return run_id, user_id


def _resolve_usage_fields(
    *,
    payload: dict,
    model: Optional[str],
    usd_micros: Optional[int],
    input_tokens: Optional[int],
    cached_input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> tuple[Optional[str], Optional[int], Optional[int], Optional[int], Optional[int]]:
    if not model:
        payload_model = payload.get("model")
        if isinstance(payload_model, str) and payload_model:
            model = payload_model

    if usd_micros is None:
        usd_micros = _optional_int(payload.get("usd_micros"))
    if input_tokens is None:
        input_tokens = _optional_int(payload.get("input_tokens"))
    if cached_input_tokens is None:
        cached_input_tokens = _optional_int(payload.get("cached_input_tokens"))
    if output_tokens is None:
        output_tokens = _optional_int(payload.get("output_tokens"))

    return model, usd_micros, input_tokens, cached_input_tokens, output_tokens


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
