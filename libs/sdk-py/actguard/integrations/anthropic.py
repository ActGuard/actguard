import importlib.util
from typing import Iterator, AsyncIterator

from actguard.core.pricing import get_cost
from actguard.core.state import get_current_state
from actguard.exceptions import BudgetExceededError

_patched = False


def _record_usage(state, model: str, input_tokens: int, output_tokens: int) -> None:
    state.tokens_used += input_tokens + output_tokens
    state.usd_used += get_cost("anthropic", model, input_tokens, output_tokens)
    from actguard.reporting import _emit_budget_consumed
    _emit_budget_consumed(state, model, input_tokens, output_tokens)


def _check_limits(state) -> None:
    if state.token_limit is not None and state.tokens_used >= state.token_limit:
        from actguard.reporting import _emit_budget_blocked
        _emit_budget_blocked(state)
        raise BudgetExceededError(
            user_id=state.user_id,
            tokens_used=state.tokens_used,
            usd_used=state.usd_used,
            token_limit=state.token_limit,
            usd_limit=state.usd_limit,
            limit_type="token",
        )
    if state.usd_limit is not None and state.usd_used >= state.usd_limit:
        from actguard.reporting import _emit_budget_blocked
        _emit_budget_blocked(state)
        raise BudgetExceededError(
            user_id=state.user_id,
            tokens_used=state.tokens_used,
            usd_used=state.usd_used,
            token_limit=state.token_limit,
            usd_limit=state.usd_limit,
            limit_type="usd",
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

    from anthropic._base_client import SyncAPIClient, AsyncAPIClient

    _orig_request = SyncAPIClient.request
    _orig_async_request = AsyncAPIClient.request

    def _request(self, cast_to, options, *, stream=False, stream_cls=None):
        state = get_current_state()
        if state is None:
            return _orig_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)
        if not _is_messages_endpoint(options):
            return _orig_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)

        from actguard.reporting import _emit_budget_check
        _emit_budget_check(state)
        _check_limits(state)
        model = _get_model_from_options(options)

        if stream:
            result = _orig_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)
            return _WrappedSyncStream(result, model, state)

        response = _orig_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)
        if response.usage is not None:
            _record_usage(
                state,
                model,
                getattr(response.usage, "input_tokens", 0) or 0,
                getattr(response.usage, "output_tokens", 0) or 0,
            )
        _check_limits(state)
        return response

    async def _async_request(self, cast_to, options, *, stream=False, stream_cls=None):
        state = get_current_state()
        if state is None:
            return await _orig_async_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)
        if not _is_messages_endpoint(options):
            return await _orig_async_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)

        from actguard.reporting import _emit_budget_check
        _emit_budget_check(state)
        _check_limits(state)
        model = _get_model_from_options(options)

        if stream:
            result = await _orig_async_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)
            return _WrappedAsyncStream(result, model, state)

        response = await _orig_async_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)
        if response.usage is not None:
            _record_usage(
                state,
                model,
                getattr(response.usage, "input_tokens", 0) or 0,
                getattr(response.usage, "output_tokens", 0) or 0,
            )
        _check_limits(state)
        return response

    SyncAPIClient.request = _request
    AsyncAPIClient.request = _async_request
    _patched = True
