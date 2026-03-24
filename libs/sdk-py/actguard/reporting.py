from __future__ import annotations

from typing import Any, Optional

from actguard.core.budget_context import (
    check_budget_limits,
    get_budget_state,
    record_usage,
)
from actguard.core.budget_recorder import get_current_budget_recorder
from actguard.exceptions import BudgetExceededError
from actguard.integrations.usage import extract_usage_info
from actguard.observability.events import emit_event, emit_usage_event
from actguard.observability.violations import emit_violation


def _budget_accounting_active() -> bool:
    return get_current_budget_recorder() is not None or get_budget_state() is not None


def emit_provider_usage_event(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> None:
    if _budget_accounting_active():
        return

    emit_usage_event(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
    )


def record_response_usage(
    response: Any,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> bool:
    usage = extract_usage_info(response, provider=provider, model=model)
    if usage is None:
        return False

    recorder = get_current_budget_recorder()
    if recorder is not None:
        recorder.record_usage(
            provider=usage.provider,
            provider_model_id=usage.model,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
        )
    elif get_budget_state() is not None:
        record_usage(
            provider=usage.provider,
            provider_model_id=usage.model,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
        )

    emit_provider_usage_event(
        provider=usage.provider,
        model=usage.model,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
    )
    _check_limits()
    return True


def _check_limits() -> None:
    recorder = get_current_budget_recorder()
    if recorder is not None:
        recorder.check_limits()
        return

    violation = check_budget_limits()
    if violation is None:
        return

    from actguard.budget_events import emit_budget_blocked

    blocked_scope = violation.blocked_scope
    emit_budget_blocked(blocked_scope)
    raise BudgetExceededError(
        user_id=blocked_scope.user_id,
        tokens_used=blocked_scope.tokens_used,
        cost_used=blocked_scope.cost_used,
        cost_limit=blocked_scope.cost_limit,
        limit_type="cost",
        scope_id=blocked_scope.scope_id,
        scope_name=blocked_scope.scope_name,
        scope_kind=blocked_scope.scope_kind,
        parent_scope_id=blocked_scope.parent_scope_id,
        root_scope_id=blocked_scope.root_scope_id,
    )


__all__ = [
    "emit_event",
    "emit_provider_usage_event",
    "emit_usage_event",
    "emit_violation",
    "record_response_usage",
]
