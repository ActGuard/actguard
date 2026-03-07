from __future__ import annotations

import os
from typing import Any, Mapping, Optional

_MAX_ERROR_MESSAGE_LEN = 256


def emit_all_tool_runs_enabled() -> bool:
    raw = os.getenv("ACTGUARD_EMIT_ALL_TOOL_RUNS", "")
    return raw.lower() in {"1", "true", "yes", "on"}


def emit_tool_failure(tool_name: str, exc: BaseException) -> None:
    payload: dict[str, Any] = {
        "tool_name": tool_name,
        "error_type": type(exc).__name__,
    }
    message = _safe_message(exc)
    if message:
        payload["error_message"] = message

    _emit("tool", "failure", payload, severity="error", outcome="failed")


def emit_guard_blocked(
    tool_name: str,
    guard_name: str,
    exc: BaseException,
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    payload: dict[str, Any] = {
        "tool_name": tool_name,
        "guard_name": guard_name,
        "error_type": type(exc).__name__,
    }
    message = _safe_message(exc)
    if message:
        payload["error_message"] = message
    if extra:
        payload.update(extra)

    _emit("guard", "blocked", payload, severity="error", outcome="blocked")


def emit_guard_intervention(
    tool_name: str,
    guard_name: str,
    *,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    payload: dict[str, Any] = {
        "tool_name": tool_name,
        "guard_name": guard_name,
    }
    if extra:
        payload.update(extra)

    _emit("guard", "intervention", payload, outcome="intervened")


def _safe_message(exc: BaseException) -> str:
    try:
        raw = str(exc)
    except Exception:
        return ""
    if len(raw) <= _MAX_ERROR_MESSAGE_LEN:
        return raw
    return raw[: _MAX_ERROR_MESSAGE_LEN - 3] + "..."


def _emit(category: str, name: str, payload: dict, **kwargs) -> None:
    try:
        from actguard.reporting import emit_event

        emit_event(category, name, payload, **kwargs)
    except Exception:
        pass
