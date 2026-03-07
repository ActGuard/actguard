import importlib.util
import json
import re
import warnings
from typing import Any, Optional

from actguard.core.pricing import get_cost
from actguard.core.state import get_current_state
from actguard.exceptions import BudgetExceededError

_patched = False
_MIN_GOOGLE_GENAI_VERSION = (0, 8)


def _record_usage(state, model: str, input_tokens: int, output_tokens: int) -> None:
    state.record_usage(
        provider="google",
        provider_model_id=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    state.usd_used += get_cost("google", model, input_tokens, output_tokens)
    from actguard.reporting import _emit_budget_consumed
    _emit_budget_consumed(state, model, input_tokens, output_tokens)


def _check_limits(state) -> None:
    if state.usd_limit is not None and state.usd_used >= state.usd_limit:
        from actguard.reporting import _emit_budget_blocked
        _emit_budget_blocked(state)
        raise BudgetExceededError(
            user_id=state.user_id,
            tokens_used=state.tokens_used,
            usd_used=state.usd_used,
            usd_limit=state.usd_limit,
            limit_type="usd",
        )


def _parse_major_minor(version: str) -> Optional[tuple[int, int]]:
    m = re.match(r"^(\d+)\.(\d+)", version or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _warn_if_old_version(genai_module) -> None:
    version = getattr(genai_module, "__version__", "")
    parsed = _parse_major_minor(version)
    if parsed is None:
        return
    if parsed < _MIN_GOOGLE_GENAI_VERSION:
        warnings.warn(
            (
                "actguard low-level google-genai patch expects "
                f"google-genai>={_MIN_GOOGLE_GENAI_VERSION[0]}.{_MIN_GOOGLE_GENAI_VERSION[1]}; "
                f"detected {version}. Budget tracking may fail with this SDK version."
            ),
            UserWarning,
            stacklevel=2,
        )


def _get_field(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_usage_tokens(payload: Any) -> Optional[tuple[int, int]]:
    payload = _payload_from_response(payload)
    if payload is None:
        return None
    usage = _get_field(payload, "usageMetadata", "usage_metadata")
    if usage is None:
        return None
    inp = _to_int(_get_field(usage, "promptTokenCount", "prompt_token_count"))
    out = _to_int(_get_field(usage, "candidatesTokenCount", "candidates_token_count"))
    return inp, out


def _payload_from_response(payload: Any) -> Any:
    if payload is None:
        return None

    body = _get_field(payload, "body")
    if body is None:
        return payload

    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        if not body.strip():
            return None
        try:
            return json.loads(body)
        except (TypeError, ValueError):
            return None
    return payload


def _extract_path(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    for key in ("path", "url", "request_path", "rpc_path"):
        value = kwargs.get(key)
        if isinstance(value, str):
            return value

    first_str = ""
    for arg in args:
        if not isinstance(arg, str):
            continue
        if not first_str:
            first_str = arg
        if ":generateContent" in arg or ":streamGenerateContent" in arg:
            return arg
        if "/models/" in arg or arg.startswith("models/"):
            return arg

    return first_str


def _is_generate_path(path: str) -> bool:
    return ":generateContent" in path or ":streamGenerateContent" in path


def _model_from_path(path: str) -> str:
    m = re.search(r"(?:^|/)models/([^:/?]+)", path or "")
    if m:
        return m.group(1)

    m = re.search(r"(?:^|/)([^/:?]+):(?:stream)?generateContent", path or "")
    if m:
        return m.group(1)
    return ""


class _WrappedSyncStream:
    """Transparent proxy around a google-genai sync streamed response."""

    def __init__(self, inner, model: str, state) -> None:
        self._inner = inner
        self._iter = iter(inner)
        self._model = model
        self._state = state
        self._final_usage: Optional[tuple[int, int]] = None
        self._recorded = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = next(self._iter)
        except StopIteration:
            self._finalize()
            raise

        tokens = _extract_usage_tokens(chunk)
        if tokens is not None:
            self._final_usage = tokens
        return chunk

    def _finalize(self) -> None:
        if self._recorded:
            return
        if self._final_usage is not None:
            _record_usage(self._state, self._model, *self._final_usage)
            _check_limits(self._state)
        self._recorded = True

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _WrappedAsyncStream:
    """Transparent proxy around a google-genai async streamed response."""

    def __init__(self, inner, model: str, state) -> None:
        self._inner = inner
        self._aiter = None
        self._model = model
        self._state = state
        self._final_usage: Optional[tuple[int, int]] = None
        self._recorded = False

    def __aiter__(self):
        self._aiter = self._inner.__aiter__()
        return self

    async def __anext__(self):
        if self._aiter is None:
            self._aiter = self._inner.__aiter__()

        try:
            chunk = await self._aiter.__anext__()
        except StopAsyncIteration:
            self._finalize()
            raise

        tokens = _extract_usage_tokens(chunk)
        if tokens is not None:
            self._final_usage = tokens
        return chunk

    def _finalize(self) -> None:
        if self._recorded:
            return
        if self._final_usage is not None:
            _record_usage(self._state, self._model, *self._final_usage)
            _check_limits(self._state)
        self._recorded = True

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def __aenter__(self):
        if hasattr(type(self._inner), '__aenter__'):
            await self._inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        if hasattr(type(self._inner), '__aexit__'):
            return await self._inner.__aexit__(*args)


def patch_google() -> None:
    global _patched
    if _patched:
        return

    try:
        found = importlib.util.find_spec("google.genai")
    except ModuleNotFoundError:
        found = None
    if found is None:
        return

    import google.genai as genai
    from google.genai._api_client import BaseApiClient

    _warn_if_old_version(genai)

    _orig_request = getattr(BaseApiClient, "request", None)
    _orig_request_streamed = getattr(BaseApiClient, "request_streamed", None)
    _orig_async_request = getattr(BaseApiClient, "async_request", None)
    _orig_async_request_streamed = getattr(BaseApiClient, "async_request_streamed", None)

    if not all(callable(fn) for fn in (_orig_request, _orig_request_streamed, _orig_async_request, _orig_async_request_streamed)):
        return

    def _request(self, *args, **kwargs):
        state = get_current_state()
        path = _extract_path(args, kwargs)

        if state is None or not _is_generate_path(path):
            return _orig_request(self, *args, **kwargs)

        from actguard.reporting import _emit_budget_check
        _emit_budget_check(state)
        _check_limits(state)
        model = _model_from_path(path)

        result = _orig_request(self, *args, **kwargs)
        tokens = _extract_usage_tokens(result)
        if tokens is not None:
            _record_usage(state, model, *tokens)
        _check_limits(state)
        return result

    def _request_streamed(self, *args, **kwargs):
        state = get_current_state()
        path = _extract_path(args, kwargs)

        if state is None or not _is_generate_path(path):
            return _orig_request_streamed(self, *args, **kwargs)

        from actguard.reporting import _emit_budget_check
        _emit_budget_check(state)
        _check_limits(state)
        model = _model_from_path(path)

        result = _orig_request_streamed(self, *args, **kwargs)
        return _WrappedSyncStream(result, model, state)

    async def _async_request(self, *args, **kwargs):
        state = get_current_state()
        path = _extract_path(args, kwargs)

        if state is None or not _is_generate_path(path):
            return await _orig_async_request(self, *args, **kwargs)

        from actguard.reporting import _emit_budget_check
        _emit_budget_check(state)
        _check_limits(state)
        model = _model_from_path(path)

        result = await _orig_async_request(self, *args, **kwargs)
        tokens = _extract_usage_tokens(result)
        if tokens is not None:
            _record_usage(state, model, *tokens)
        _check_limits(state)
        return result

    async def _async_request_streamed(self, *args, **kwargs):
        state = get_current_state()
        path = _extract_path(args, kwargs)

        if state is None or not _is_generate_path(path):
            return await _orig_async_request_streamed(self, *args, **kwargs)

        from actguard.reporting import _emit_budget_check
        _emit_budget_check(state)
        _check_limits(state)
        model = _model_from_path(path)

        result = await _orig_async_request_streamed(self, *args, **kwargs)
        return _WrappedAsyncStream(result, model, state)

    BaseApiClient.request = _request
    BaseApiClient.request_streamed = _request_streamed
    BaseApiClient.async_request = _async_request
    BaseApiClient.async_request_streamed = _async_request_streamed
    _patched = True
