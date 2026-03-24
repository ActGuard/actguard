import importlib.util
from typing import AsyncIterator, Iterator

from actguard.budget_events import emit_budget_blocked
from actguard.core.budget_context import (
    check_budget_limits,
    get_budget_state,
    record_usage,
)
from actguard.core.budget_recorder import get_current_budget_recorder
from actguard.exceptions import BudgetExceededError
from actguard.integrations.usage import extract_usage_info
from actguard.reporting import emit_provider_usage_event

_patched = False


def _record_usage(state, model: str, input_tokens: int, output_tokens: int) -> None:
    recorder = get_current_budget_recorder()
    if recorder is not None:
        recorder.record_usage(
            provider="anthropic",
            provider_model_id=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    elif get_budget_state() is None:
        state.record_usage(
            provider="anthropic",
            provider_model_id=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    else:
        record_usage(
            provider="anthropic",
            provider_model_id=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    emit_provider_usage_event(
        provider="anthropic",
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=0,
        output_tokens=output_tokens,
    )


def _check_limits(state) -> None:
    recorder = get_current_budget_recorder()
    if recorder is not None:
        recorder.check_limits()
        return
    if get_budget_state() is None:
        if state.cost_limit is not None and state.cost_used >= state.cost_limit:
            emit_budget_blocked(state)
            raise BudgetExceededError(
                user_id=state.user_id,
                tokens_used=state.tokens_used,
                cost_used=state.cost_used,
                cost_limit=state.cost_limit,
                limit_type="cost",
                scope_id=state.scope_id,
                scope_name=state.scope_name,
                scope_kind=state.scope_kind,
                parent_scope_id=state.parent_scope_id,
                root_scope_id=state.root_scope_id,
            )
        return
    violation = check_budget_limits()
    if violation is not None:
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


class _WrappedSyncStream:
    """Transparent proxy around an Anthropic sync streaming response.

    Tracks ``message_start`` (input tokens) and ``message_delta`` (output tokens)
    SSE events and records usage after the stream is exhausted.
    """

    def __init__(self, inner, model: str, state) -> None:
        self._inner = inner
        self._model = model
        self._state = state

    def __iter__(self) -> Iterator:
        input_tokens = 0
        output_tokens = 0
        for event in self._inner:
            yield event
            event_type = getattr(event, "type", None)
            if event_type == "message_start":
                usage = getattr(getattr(event, "message", None), "usage", None)
                if usage is not None:
                    input_tokens = getattr(usage, "input_tokens", 0) or 0
            elif event_type == "message_delta":
                usage = getattr(event, "usage", None)
                if usage is not None:
                    output_tokens = getattr(usage, "output_tokens", 0) or 0

        _record_usage(self._state, self._model, input_tokens, output_tokens)
        _check_limits(self._state)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *args):
        return self._inner.__exit__(*args)


class _WrappedAsyncStream:
    """Transparent proxy around an Anthropic async streaming response."""

    def __init__(self, inner, model: str, state) -> None:
        self._inner = inner
        self._model = model
        self._state = state

    def __aiter__(self) -> AsyncIterator:
        return self._aiter_impl()

    async def _aiter_impl(self):
        input_tokens = 0
        output_tokens = 0
        async for event in self._inner:
            yield event
            event_type = getattr(event, "type", None)
            if event_type == "message_start":
                usage = getattr(getattr(event, "message", None), "usage", None)
                if usage is not None:
                    input_tokens = getattr(usage, "input_tokens", 0) or 0
            elif event_type == "message_delta":
                usage = getattr(event, "usage", None)
                if usage is not None:
                    output_tokens = getattr(usage, "output_tokens", 0) or 0

        _record_usage(self._state, self._model, input_tokens, output_tokens)
        _check_limits(self._state)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def __aenter__(self):
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self._inner.__aexit__(*args)


def _is_messages_endpoint(options) -> bool:
    return "/v1/messages" in str(getattr(options, "url", ""))


def _get_model_from_options(options) -> str:
    if isinstance(options.json_data, dict):
        model = options.json_data.get("model", "")
        if isinstance(model, str):
            return model
    return ""


def patch_anthropic() -> None:
    global _patched
    if _patched:
        return
    if importlib.util.find_spec("anthropic") is None:
        return

    from anthropic._base_client import AsyncAPIClient, SyncAPIClient

    _orig_request = SyncAPIClient.request
    _orig_async_request = AsyncAPIClient.request

    def _request(self, cast_to, options, *, stream=False, stream_cls=None):
        state = get_budget_state()
        if state is None and get_current_budget_recorder() is None:
            return _orig_request(
                self, cast_to, options, stream=stream, stream_cls=stream_cls
            )
        if not _is_messages_endpoint(options):
            return _orig_request(
                self, cast_to, options, stream=stream, stream_cls=stream_cls
            )

        _check_limits(state)
        model = _get_model_from_options(options)

        if stream:
            result = _orig_request(
                self, cast_to, options, stream=stream, stream_cls=stream_cls
            )
            return _WrappedSyncStream(result, model, state)

        response = _orig_request(
            self, cast_to, options, stream=stream, stream_cls=stream_cls
        )
        usage = extract_usage_info(response, provider="anthropic", model=model)
        if usage is not None:
            _record_usage(
                state,
                usage.model,
                usage.input_tokens,
                usage.output_tokens,
            )
        _check_limits(state)
        return response

    async def _async_request(self, cast_to, options, *, stream=False, stream_cls=None):
        state = get_budget_state()
        if state is None and get_current_budget_recorder() is None:
            return await _orig_async_request(
                self, cast_to, options, stream=stream, stream_cls=stream_cls
            )
        if not _is_messages_endpoint(options):
            return await _orig_async_request(
                self, cast_to, options, stream=stream, stream_cls=stream_cls
            )

        _check_limits(state)
        model = _get_model_from_options(options)

        if stream:
            result = await _orig_async_request(
                self, cast_to, options, stream=stream, stream_cls=stream_cls
            )
            return _WrappedAsyncStream(result, model, state)

        response = await _orig_async_request(
            self, cast_to, options, stream=stream, stream_cls=stream_cls
        )
        usage = extract_usage_info(response, provider="anthropic", model=model)
        if usage is not None:
            _record_usage(
                state,
                usage.model,
                usage.input_tokens,
                usage.output_tokens,
            )
        _check_limits(state)
        return response

    SyncAPIClient.request = _request
    AsyncAPIClient.request = _async_request
    _patched = True
