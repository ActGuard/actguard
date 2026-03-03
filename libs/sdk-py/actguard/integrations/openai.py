import importlib.util
import re
from typing import Iterator

from actguard.core.pricing import get_cost
from actguard.core.state import get_current_state
from actguard.exceptions import BudgetExceededError

_patched = False


def _parse_major_minor(version: str):
    m = re.match(r"^(\d+)\.(\d+)", version or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _record_usage(state, model: str, input_tokens: int, output_tokens: int) -> None:
    state.tokens_used += input_tokens + output_tokens
    state.usd_used += get_cost("openai", model, input_tokens, output_tokens)
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


def _get_usage_tokens(usage) -> tuple:
    inp = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None) or 0
    out = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None) or 0
    return inp, out


def _try_stream_usage(chunk):
    """Return (input_tokens, output_tokens) if this chunk carries final usage, else None."""
    # Chat Completions: usage on the chunk itself
    usage = getattr(chunk, "usage", None)
    if usage is not None:
        return _get_usage_tokens(usage)
    # Responses API: usage on response.completed event
    if getattr(chunk, "type", None) == "response.completed":
        resp = getattr(chunk, "response", None)
        usage = getattr(resp, "usage", None) if resp else None
        if usage is not None:
            return _get_usage_tokens(usage)
    return None


def _get_model_from_options(options) -> str:
    """Return the model name from request options, or '' for GET requests (json_data=None)."""
    if isinstance(options.json_data, dict):
        return options.json_data.get("model", "")
    return ""


def _inject_stream_options(options) -> None:
    """Inject stream_options only for chat/completions endpoints to avoid 400s elsewhere."""
    if isinstance(options.json_data, dict) and "chat/completions" in str(getattr(options, "url", "")):
        options.json_data.setdefault("stream_options", {"include_usage": True})


class _WrappedSyncStream:
    """Transparent proxy around an OpenAI sync streaming response."""

    def __init__(self, inner, model: str, state) -> None:
        self._inner = inner
        self._model = model
        self._state = state

    def __iter__(self) -> Iterator:
        for chunk in self._inner:
            yield chunk
            tokens = _try_stream_usage(chunk)
            if tokens is not None:
                _record_usage(self._state, self._model, *tokens)
                _check_limits(self._state)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _WrappedAsyncStream:
    """Transparent proxy around an OpenAI async streaming response."""

    def __init__(self, inner, model: str, state) -> None:
        self._inner = inner
        self._model = model
        self._state = state
        self._aiter = None

    def __aiter__(self):
        self._aiter = self._inner.__aiter__()
        return self

    async def __anext__(self):
        if self._aiter is None:
            self._aiter = self._inner.__aiter__()
        chunk = await self._aiter.__anext__()  # StopAsyncIteration propagates naturally
        tokens = _try_stream_usage(chunk)
        if tokens is not None:
            _record_usage(self._state, self._model, *tokens)
            _check_limits(self._state)
        return chunk

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def patch_openai() -> None:
    global _patched
    if _patched:
        return
    if importlib.util.find_spec("openai") is None:
        return

    import openai as _oai_pkg
    _ver = _parse_major_minor(getattr(_oai_pkg, "__version__", ""))
    if _ver is not None and _ver < (1, 76):
        import warnings
        warnings.warn(
            f"actguard requires openai>=1.76.0; detected {_oai_pkg.__version__}. "
            "Budget tracking may fail with this SDK version.",
            UserWarning,
            stacklevel=2,
        )

    from openai._base_client import SyncAPIClient, AsyncAPIClient

    _orig_request = SyncAPIClient.request
    _orig_async_request = AsyncAPIClient.request

    def _request(self, cast_to, options, *, stream=False, stream_cls=None):
        state = get_current_state()
        if state is None:
            return _orig_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)

        from actguard.reporting import _emit_budget_check
        _emit_budget_check(state)
        _check_limits(state)
        model = _get_model_from_options(options)
        if stream:
            _inject_stream_options(options)

        result = _orig_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)

        if stream:
            return _WrappedSyncStream(result, model, state)

        usage = getattr(result, "usage", None)
        if usage is not None:
            inp, out = _get_usage_tokens(usage)
            _record_usage(state, model, inp, out)
        _check_limits(state)
        return result

    async def _async_request(self, cast_to, options, *, stream=False, stream_cls=None):
        state = get_current_state()
        if state is None:
            return await _orig_async_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)

        from actguard.reporting import _emit_budget_check
        _emit_budget_check(state)
        _check_limits(state)
        model = _get_model_from_options(options)
        if stream:
            _inject_stream_options(options)

        result = await _orig_async_request(self, cast_to, options, stream=stream, stream_cls=stream_cls)

        if stream:
            return _WrappedAsyncStream(result, model, state)

        usage = getattr(result, "usage", None)
        if usage is not None:
            inp, out = _get_usage_tokens(usage)
            _record_usage(state, model, inp, out)
        _check_limits(state)
        return result

    SyncAPIClient.request = _request
    AsyncAPIClient.request = _async_request
    _patched = True
