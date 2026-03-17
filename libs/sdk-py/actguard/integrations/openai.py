import importlib.util
import re
from typing import Iterator

from actguard.budget_events import emit_budget_blocked
from actguard.core.budget_context import (
    check_budget_limits,
    get_budget_state,
    record_usage,
)
from actguard.core.budget_recorder import get_current_budget_recorder
from actguard.exceptions import BudgetExceededError
from actguard.reporting import emit_usage_event

_patched = False


def _parse_major_minor(version: str):
    m = re.match(r"^(\d+)\.(\d+)", version or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _record_usage(
    state,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> None:
    recorder = get_current_budget_recorder()
    if recorder is not None:
        recorder.record_usage(
            provider="openai",
            provider_model_id=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
    elif get_budget_state() is None:
        state.record_usage(
            provider="openai",
            provider_model_id=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
    else:
        record_usage(
            provider="openai",
            provider_model_id=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )
    emit_usage_event(
        provider="openai",
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
    )


def _check_limits(state) -> None:
    recorder = get_current_budget_recorder()
    if recorder is not None:
        recorder.check_limits()
        return
    if get_budget_state() is None:
        if state.usd_limit is not None and state.usd_used >= state.usd_limit:
            emit_budget_blocked(state)
            raise BudgetExceededError(
                user_id=state.user_id,
                tokens_used=state.tokens_used,
                usd_used=state.usd_used,
                usd_limit=state.usd_limit,
                limit_type="usd",
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
            usd_used=blocked_scope.usd_used,
            usd_limit=blocked_scope.usd_limit,
            limit_type="usd",
            scope_id=blocked_scope.scope_id,
            scope_name=blocked_scope.scope_name,
            scope_kind=blocked_scope.scope_kind,
            parent_scope_id=blocked_scope.parent_scope_id,
            root_scope_id=blocked_scope.root_scope_id,
        )


def _get_cached_input_tokens(usage) -> int:
    details = getattr(usage, "prompt_tokens_details", None) or getattr(
        usage, "input_tokens_details", None
    )
    if details is None:
        return 0
    return (
        getattr(details, "cached_tokens", None)
        or getattr(details, "cached_input_tokens", None)
        or 0
    )


def _get_usage_tokens(usage) -> tuple:
    inp = (
        getattr(usage, "prompt_tokens", None)
        or getattr(usage, "input_tokens", None)
        or 0
    )
    out = (
        getattr(usage, "completion_tokens", None)
        or getattr(usage, "output_tokens", None)
        or 0
    )
    cached = _get_cached_input_tokens(usage)
    return inp, out, cached


def _try_stream_usage(chunk):
    """Return final usage tokens when a streaming chunk carries usage."""
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
    """Return model name from request options, or ``""`` for GET requests."""
    if isinstance(options.json_data, dict):
        return options.json_data.get("model", "")
    return ""


def _inject_stream_options(options) -> None:
    """Inject stream usage options only for chat/completions endpoints."""
    if isinstance(options.json_data, dict) and "chat/completions" in str(
        getattr(options, "url", "")
    ):
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

    async def __aenter__(self):
        if hasattr(type(self._inner), "__aenter__"):
            await self._inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        if hasattr(type(self._inner), "__aexit__"):
            return await self._inner.__aexit__(*args)


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

    from openai._base_client import AsyncAPIClient, SyncAPIClient

    _orig_request = SyncAPIClient.request
    _orig_async_request = AsyncAPIClient.request

    def _request(self, cast_to, options, *, stream=False, stream_cls=None):
        state = get_budget_state()
        if state is None and get_current_budget_recorder() is None:
            return _orig_request(
                self, cast_to, options, stream=stream, stream_cls=stream_cls
            )

        _check_limits(state)
        model = _get_model_from_options(options)
        if stream:
            _inject_stream_options(options)

        result = _orig_request(
            self, cast_to, options, stream=stream, stream_cls=stream_cls
        )

        if stream:
            return _WrappedSyncStream(result, model, state)

        usage = getattr(result, "usage", None)
        if usage is not None:
            inp, out, cached = _get_usage_tokens(usage)
            _record_usage(state, model, inp, out, cached)
        _check_limits(state)
        return result

    async def _async_request(self, cast_to, options, *, stream=False, stream_cls=None):
        state = get_budget_state()
        if state is None and get_current_budget_recorder() is None:
            return await _orig_async_request(
                self, cast_to, options, stream=stream, stream_cls=stream_cls
            )

        _check_limits(state)
        model = _get_model_from_options(options)
        if stream:
            _inject_stream_options(options)

        result = await _orig_async_request(
            self, cast_to, options, stream=stream, stream_cls=stream_cls
        )

        if stream:
            return _WrappedAsyncStream(result, model, state)

        usage = getattr(result, "usage", None)
        if usage is not None:
            inp, out, cached = _get_usage_tokens(usage)
            _record_usage(state, model, inp, out, cached)
        _check_limits(state)
        return result

    SyncAPIClient.request = _request
    AsyncAPIClient.request = _async_request
    _patched = True
