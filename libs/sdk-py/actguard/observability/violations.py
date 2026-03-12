from __future__ import annotations

from actguard._monitoring import warn_monitoring_issue

from .events import emit_event


def emit_violation(error: Exception, **context_overrides) -> None:
    """Emit a structured event for an ActGuard error with reporting metadata."""
    client, config = _runtime_event_client_and_config()
    if client is None or config is None:
        return

    from actguard.events.envelope import ActGuardContextEvidenceProvider
    from actguard.exceptions import ActGuardError
    from actguard.plugins import get_plugins

    if not isinstance(error, ActGuardError) or not error.is_reportable:
        return

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

    try:
        emit_event(
            error.event_category,
            error.event_name,
            err_payload,
            severity=error.severity,
            outcome=error.outcome,
            evidence=ev_list,
            **context_overrides,
        )
    except Exception as exc:
        warn_monitoring_issue(
            subsystem="reporting",
            operation=f"{error.event_category}.{error.event_name}",
            exc=exc,
            stacklevel=2,
        )


def _runtime_event_client_and_config():
    from .events import (
        _runtime_event_client_and_config as _get_runtime_event_client_and_config,
    )

    return _get_runtime_event_client_and_config()
