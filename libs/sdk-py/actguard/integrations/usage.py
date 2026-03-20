from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator, Optional


@dataclass(frozen=True)
class UsageInfo:
    provider: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


def extract_usage_info(
    response: Any,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[UsageInfo]:
    for candidate in _iter_candidates(response):
        tokens = _extract_usage_tokens(candidate)
        if tokens is None:
            continue
        resolved_model = _resolve_model(candidate, default=model)
        resolved_provider = _resolve_provider(
            candidate,
            default=provider,
            model=resolved_model or model,
        )
        if not resolved_provider or not resolved_model:
            continue
        return UsageInfo(
            provider=resolved_provider,
            model=resolved_model,
            input_tokens=tokens[0],
            cached_input_tokens=tokens[1],
            output_tokens=tokens[2],
        )
    return None


def _iter_candidates(response: Any) -> Iterator[Any]:
    seen: set[int] = set()
    queue = [response]
    while queue:
        candidate = queue.pop(0)
        marker = id(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        yield candidate
        raw = _get_field(candidate, "raw")
        if raw is not None:
            queue.append(raw)


def _extract_usage_tokens(candidate: Any) -> Optional[tuple[int, int, int]]:
    usage_metadata = _get_field(candidate, "usage_metadata")
    tokens = _extract_tokens_from_usage(usage_metadata)
    if tokens is not None:
        return tokens

    response_metadata = _get_field(candidate, "response_metadata")
    token_usage = _get_field(response_metadata, "token_usage", "usage")
    tokens = _extract_tokens_from_usage(token_usage)
    if tokens is not None:
        return tokens

    usage = _get_field(candidate, "usage")
    tokens = _extract_tokens_from_usage(usage)
    if tokens is not None:
        return tokens

    payload = _payload_from_response(candidate)
    if payload is None or payload is candidate:
        return None
    usage = _get_field(payload, "usageMetadata", "usage_metadata")
    return _extract_tokens_from_usage(usage)


def _extract_tokens_from_usage(usage: Any) -> Optional[tuple[int, int, int]]:
    if usage is None:
        return None
    inp = _first_int(
        usage,
        "prompt_tokens",
        "input_tokens",
        "promptTokenCount",
        "prompt_token_count",
    )
    out = _first_int(
        usage,
        "completion_tokens",
        "output_tokens",
        "candidatesTokenCount",
        "candidates_token_count",
    )
    cached = _extract_cached_input_tokens(usage)
    if inp is None and out is None:
        return None
    return (inp or 0, cached, out or 0)


def _extract_cached_input_tokens(usage: Any) -> int:
    details = _get_field(
        usage,
        "prompt_tokens_details",
        "input_tokens_details",
        "input_token_details",
    )
    cached = _first_int(
        details,
        "cached_tokens",
        "cached_input_tokens",
        "cache_read",
        "cache_read_input_tokens",
    )
    if cached is not None:
        return cached
    direct = _first_int(
        usage,
        "cached_tokens",
        "cached_input_tokens",
        "cache_read",
        "cache_read_input_tokens",
    )
    return direct or 0


def _resolve_model(candidate: Any, *, default: Optional[str]) -> str:
    response_metadata = _get_field(candidate, "response_metadata")
    for value in (
        _get_field(response_metadata, "model_name", "model"),
        _get_field(candidate, "model_name", "model"),
        default,
    ):
        if isinstance(value, str) and value:
            return value
    return ""


def _resolve_provider(
    candidate: Any,
    *,
    default: Optional[str],
    model: Optional[str],
) -> str:
    response_metadata = _get_field(candidate, "response_metadata")
    for value in (
        default,
        _get_field(response_metadata, "model_provider", "provider"),
        _get_field(candidate, "provider"),
    ):
        normalized = _normalize_provider(value)
        if normalized:
            return normalized

    if _get_field(response_metadata, "token_usage") is not None or _get_field(
        response_metadata, "system_fingerprint"
    ) is not None:
        return "openai"
    if _get_field(response_metadata, "usage") is not None:
        return "anthropic"

    payload = _payload_from_response(candidate)
    if payload is not None and payload is not candidate:
        usage = _get_field(payload, "usageMetadata", "usage_metadata")
        if usage is not None:
            return "google"

    return _infer_provider_from_model(model)


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


def _get_field(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _first_int(obj: Any, *names: str) -> Optional[int]:
    for name in names:
        value = _get_field(obj, name)
        parsed = _to_int(value)
        if parsed is not None:
            return parsed
    return None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_provider(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"google_genai", "google_generative_ai"}:
        return "google"
    if normalized.startswith("openai") or normalized == "azure_openai":
        return "openai"
    if normalized.startswith("anthropic"):
        return "anthropic"
    if normalized.startswith("google"):
        return "google"
    return normalized


def _infer_provider_from_model(model: Optional[str]) -> str:
    if not isinstance(model, str) or not model:
        return ""
    normalized = model.lower()
    if normalized.startswith("claude"):
        return "anthropic"
    if normalized.startswith("gemini"):
        return "google"
    if (
        normalized.startswith("gpt")
        or normalized.startswith("o1")
        or normalized.startswith("o3")
    ):
        return "openai"
    return ""
