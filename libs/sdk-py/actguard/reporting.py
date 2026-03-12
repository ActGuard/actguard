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
    provider: Optional[str] = None,
    usd_micros: Optional[int] = None,
    input_tokens: Optional[int] = None,
    cached_input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    tool_name: Optional[str] = None,
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

    payload = _with_runtime_metadata(payload)

    from actguard.events.envelope import Envelope

    (
        model,
        provider,
        usd_micros,
        input_tokens,
        cached_input_tokens,
        output_tokens,
        tool_name,
        scope_fields,
        plan_key,
    ) = _resolve_usage_fields(
        payload=payload,
        model=model,
        provider=provider,
        usd_micros=usd_micros,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        tool_name=tool_name,
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
        provider=provider,
        usd_micros=usd_micros,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        scope_id=scope_fields["scope_id"],
        scope_name=scope_fields["scope_name"],
        scope_kind=scope_fields["scope_kind"],
        parent_scope_id=scope_fields["parent_scope_id"],
        root_scope_id=scope_fields["root_scope_id"],
        plan_key=plan_key,
        tool_name=tool_name,
        payload=payload,
        evidence=evidence or [],
    )
    client.enqueue(envelope)


def emit_usage_event(
    *,
    provider: str,
    model: str,
    usd_micros: int,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    payload: Optional[dict] = None,
) -> None:
    emit_event(
        "llm",
        "usage",
        payload or {},
        provider=provider,
        model=model,
        usd_micros=usd_micros,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        outcome="success",
    )


def emit_violation(error: Exception, **context_overrides) -> None:
    """Emit a structured event for an ActGuard error with reporting metadata."""
    client, config = _runtime_event_client_and_config()
    if client is None:
        return

    if config is None:
        return

    from actguard.events.envelope import ActGuardContextEvidenceProvider
    from actguard.exceptions import ActGuardError
    from actguard.plugins import get_plugins

    if not isinstance(error, ActGuardError) or not error.is_reportable:
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

    err_payload = {}
    try:
        err_payload = error.payload()
    except Exception:
        pass

    emit_event(
        error.event_category,
        error.event_name,
        err_payload,
        severity=error.severity,
        outcome=error.outcome,
        evidence=ev_list,
        **context_overrides,
    )


def _emit_budget_blocked(state) -> None:
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
    provider: Optional[str],
    usd_micros: Optional[int],
    input_tokens: Optional[int],
    cached_input_tokens: Optional[int],
    output_tokens: Optional[int],
    tool_name: Optional[str],
) -> tuple[
    Optional[str],
    Optional[str],
    Optional[int],
    Optional[int],
    Optional[int],
    Optional[int],
    Optional[str],
    dict,
    Optional[str],
]:
    if not model:
        payload_model = payload.get("model")
        if isinstance(payload_model, str) and payload_model:
            model = payload_model
    if not provider:
        payload_provider = payload.get("provider")
        if isinstance(payload_provider, str) and payload_provider:
            provider = payload_provider

    if usd_micros is None:
        usd_micros = _optional_int(payload.get("usd_micros"))
    if input_tokens is None:
        input_tokens = _optional_int(payload.get("input_tokens"))
    if cached_input_tokens is None:
        cached_input_tokens = _optional_int(payload.get("cached_input_tokens"))
    if output_tokens is None:
        output_tokens = _optional_int(payload.get("output_tokens"))
    if not tool_name:
        payload_tool_name = payload.get("tool_name")
        if isinstance(payload_tool_name, str) and payload_tool_name:
            tool_name = payload_tool_name

    scope_fields = {
        "scope_id": _optional_str(payload.get("scope_id")),
        "scope_name": _optional_str(payload.get("scope_name")),
        "scope_kind": _optional_str(payload.get("scope_kind")),
        "parent_scope_id": _optional_str(payload.get("parent_scope_id")),
        "root_scope_id": _optional_str(payload.get("root_scope_id")),
    }
    plan_key = _optional_str(payload.get("plan_key"))

    return (
        model,
        provider,
        usd_micros,
        input_tokens,
        cached_input_tokens,
        output_tokens,
        tool_name,
        scope_fields,
        plan_key,
    )


def _with_runtime_metadata(payload: dict) -> dict:
    try:
        from actguard.core.budget_context import active_scope_metadata
        from actguard.events.context import get_tool_name

        enriched = dict(payload)
        metadata = active_scope_metadata()
        if metadata is not None:
            for key, value in metadata.items():
                enriched.setdefault(key, value)
        tool_name = get_tool_name()
        if tool_name:
            enriched.setdefault("tool_name", tool_name)
        return enriched
    except Exception:
        return payload


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    return value
