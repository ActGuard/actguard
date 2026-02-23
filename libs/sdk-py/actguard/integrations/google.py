import importlib.util

from actguard.core.pricing import get_cost
from actguard.core.state import get_current_state
from actguard.exceptions import BudgetExceededError

_patched = False


def _record_usage(state, model: str, input_tokens: int, output_tokens: int) -> None:
    state.tokens_used += input_tokens + output_tokens
    state.usd_used += get_cost("google", model, input_tokens, output_tokens)


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


def _model_name_from_self(gen_model_self) -> str:
    """Extract a normalised model name from a GenerativeModel instance."""
    raw = getattr(gen_model_self, "model_name", "") or ""
    return raw.removeprefix("models/")


def patch_google() -> None:
    global _patched
    if _patched:
        return
    try:
        found = importlib.util.find_spec("google.generativeai")
    except ModuleNotFoundError:
        found = None
    if found is None:
        return

    import google.generativeai as genai

    GenerativeModel = genai.GenerativeModel

    _orig_generate = GenerativeModel.generate_content
    _orig_generate_async = GenerativeModel.generate_content_async

    def _generate_content(self, *args, **kwargs):
        state = get_current_state()
        if state is None:
            return _orig_generate(self, *args, **kwargs)

        _check_limits(state)

        stream = kwargs.get("stream", False)
        model = _model_name_from_self(self)

        if stream:
            inner = _orig_generate(self, *args, **kwargs)
            return _wrap_sync_google_stream(inner, model, state)

        response = _orig_generate(self, *args, **kwargs)
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            _record_usage(
                state,
                model,
                getattr(usage, "prompt_token_count", 0) or 0,
                getattr(usage, "candidates_token_count", 0) or 0,
            )
        _check_limits(state)
        return response

    async def _generate_content_async(self, *args, **kwargs):
        state = get_current_state()
        if state is None:
            return await _orig_generate_async(self, *args, **kwargs)

        _check_limits(state)

        stream = kwargs.get("stream", False)
        model = _model_name_from_self(self)

        if stream:
            inner = await _orig_generate_async(self, *args, **kwargs)
            return _wrap_async_google_stream(inner, model, state)

        response = await _orig_generate_async(self, *args, **kwargs)
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            _record_usage(
                state,
                model,
                getattr(usage, "prompt_token_count", 0) or 0,
                getattr(usage, "candidates_token_count", 0) or 0,
            )
        _check_limits(state)
        return response

    GenerativeModel.generate_content = _generate_content
    GenerativeModel.generate_content_async = _generate_content_async
    _patched = True


def _wrap_sync_google_stream(inner, model: str, state):
    """Wrap a synchronous Google streaming response.

    Usage appears in the first chunk only; a ``_recorded`` flag prevents
    double-counting if the iterable is somehow re-entered.
    """

    class _WrappedSyncStream:
        def __init__(self):
            self._inner = inner
            self._recorded = False

        def __iter__(self):
            for chunk in self._inner:
                yield chunk
                if not self._recorded:
                    usage = getattr(chunk, "usage_metadata", None)
                    if usage is not None:
                        _record_usage(
                            state,
                            model,
                            getattr(usage, "prompt_token_count", 0) or 0,
                            getattr(usage, "candidates_token_count", 0) or 0,
                        )
                        _check_limits(state)
                        self._recorded = True

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

    return _WrappedSyncStream()


def _wrap_async_google_stream(inner, model: str, state):
    """Wrap an asynchronous Google streaming response."""

    class _WrappedAsyncStream:
        def __init__(self):
            self._inner = inner
            self._recorded = False

        def __aiter__(self):
            return self._aiter_impl()

        async def _aiter_impl(self):
            async for chunk in self._inner:
                yield chunk
                if not self._recorded:
                    usage = getattr(chunk, "usage_metadata", None)
                    if usage is not None:
                        _record_usage(
                            state,
                            model,
                            getattr(usage, "prompt_token_count", 0) or 0,
                            getattr(usage, "candidates_token_count", 0) or 0,
                        )
                        _check_limits(state)
                        self._recorded = True

        def __getattr__(self, name: str):
            return getattr(self._inner, name)

    return _WrappedAsyncStream()
