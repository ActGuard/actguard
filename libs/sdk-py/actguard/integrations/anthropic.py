import importlib.util
from typing import Iterator, AsyncIterator

from actguard.core.pricing import get_cost
from actguard.core.state import get_current_state
from actguard.exceptions import BudgetExceededError

_patched = False


def _record_usage(state, model: str, input_tokens: int, output_tokens: int) -> None:
    state.tokens_used += input_tokens + output_tokens
    state.usd_used += get_cost("anthropic", model, input_tokens, output_tokens)


def _check_limits(state) -> None:
    if state.token_limit is not None and state.tokens_used >= state.token_limit:
        raise BudgetExceededError(
            user_id=state.user_id,
            tokens_used=state.tokens_used,
            usd_used=state.usd_used,
            token_limit=state.token_limit,
            usd_limit=state.usd_limit,
            limit_type="token",
        )
    if state.usd_limit is not None and state.usd_used >= state.usd_limit:
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


def patch_anthropic() -> None:
    global _patched
    if _patched:
        return
    if importlib.util.find_spec("anthropic") is None:
        return

    from anthropic.resources.messages import Messages, AsyncMessages

    _orig_create = Messages.create
    _orig_async_create = AsyncMessages.create

    def _create(self, *args, **kwargs):
        state = get_current_state()
        if state is None:
            return _orig_create(self, *args, **kwargs)

        _check_limits(state)

        stream = kwargs.get("stream", False)
        model = kwargs.get("model", "")

        if stream:
            result = _orig_create(self, *args, **kwargs)
            return _WrappedSyncStream(result, model, state)

        response = _orig_create(self, *args, **kwargs)
        if response.usage is not None:
            _record_usage(
                state,
                model,
                getattr(response.usage, "input_tokens", 0) or 0,
                getattr(response.usage, "output_tokens", 0) or 0,
            )
        _check_limits(state)
        return response

    async def _async_create(self, *args, **kwargs):
        state = get_current_state()
        if state is None:
            return await _orig_async_create(self, *args, **kwargs)

        _check_limits(state)

        stream = kwargs.get("stream", False)
        model = kwargs.get("model", "")

        if stream:
            result = await _orig_async_create(self, *args, **kwargs)
            return _WrappedAsyncStream(result, model, state)

        response = await _orig_async_create(self, *args, **kwargs)
        if response.usage is not None:
            _record_usage(
                state,
                model,
                getattr(response.usage, "input_tokens", 0) or 0,
                getattr(response.usage, "output_tokens", 0) or 0,
            )
        _check_limits(state)
        return response

    Messages.create = _create
    AsyncMessages.create = _async_create
    _patched = True
