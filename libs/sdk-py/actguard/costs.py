from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class LLMCostRates:
    input_cu_per_1k: Optional[int]
    input_cu_per_1m: Optional[int]
    output_cu_per_1k: int
    cached_cu_per_1k: int

    def cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> int:
        return (
            _input_cost(
                input_tokens=input_tokens,
                input_cu_per_1k=self.input_cu_per_1k,
                input_cu_per_1m=self.input_cu_per_1m,
            )
            + _ceil_per_1k(output_tokens, self.output_cu_per_1k)
            + _ceil_per_1k(cached_input_tokens, self.cached_cu_per_1k)
        )


@dataclass(frozen=True)
class PartialLLMCostRates:
    input_cu_per_1k: Optional[int] = None
    input_cu_per_1m: Optional[int] = None
    output_cu_per_1k: Optional[int] = None
    cached_cu_per_1k: Optional[int] = None

    def resolve(self, fallback: LLMCostRates) -> LLMCostRates:
        if self.input_cu_per_1k is not None:
            input_cu_per_1k = self.input_cu_per_1k
            input_cu_per_1m = None
        elif self.input_cu_per_1m is not None:
            input_cu_per_1k = None
            input_cu_per_1m = self.input_cu_per_1m
        else:
            input_cu_per_1k = fallback.input_cu_per_1k
            input_cu_per_1m = fallback.input_cu_per_1m
        if input_cu_per_1k is not None and input_cu_per_1m is not None:
            raise ValueError(
                "CU tariff input rate cannot define both per-1k and per-1m values."
            )

        return LLMCostRates(
            input_cu_per_1k=input_cu_per_1k,
            input_cu_per_1m=input_cu_per_1m,
            output_cu_per_1k=(
                self.output_cu_per_1k
                if self.output_cu_per_1k is not None
                else fallback.output_cu_per_1k
            ),
            cached_cu_per_1k=(
                self.cached_cu_per_1k
                if self.cached_cu_per_1k is not None
                else fallback.cached_cu_per_1k
            ),
        )


@dataclass(frozen=True)
class CuTariff:
    tariff_version: str
    cu_per_usd: int
    registry_version: str
    llm_default: LLMCostRates
    llm_provider_model_overrides: dict[str, dict[str, PartialLLMCostRates]]
    tools: dict[str, int]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CuTariff":
        llm = payload.get("llm")
        if not isinstance(llm, Mapping):
            raise ValueError("CU tariff response missing 'llm' mapping.")
        default = llm.get("default")
        if not isinstance(default, Mapping):
            raise ValueError("CU tariff response missing 'llm.default' mapping.")

        tools_payload = payload.get("tools")
        if tools_payload is None:
            tools_payload = {}
        if not isinstance(tools_payload, Mapping):
            raise ValueError("CU tariff response field 'tools' must be a mapping.")
        providers_payload = llm.get("providers")
        if providers_payload is None:
            providers_payload = {}
        if not isinstance(providers_payload, Mapping):
            raise ValueError(
                "CU tariff response field 'llm.providers' must be a mapping."
            )

        tariff_version = _required_str(payload, "tariff_version")
        registry_version = _required_str(payload, "registry_version")
        return cls(
            tariff_version=tariff_version,
            cu_per_usd=_required_int(payload, "cu_per_usd"),
            registry_version=registry_version,
            llm_default=LLMCostRates(
                input_cu_per_1k=_required_int(default, "input_cu_per_1k"),
                input_cu_per_1m=None,
                output_cu_per_1k=_required_int(default, "output_cu_per_1k"),
                cached_cu_per_1k=_required_int(default, "cached_cu_per_1k"),
            ),
            llm_provider_model_overrides=_parse_provider_model_overrides(
                providers_payload
            ),
            tools={
                str(name): _coerce_non_negative_int(value, f"tools.{name}")
                for name, value in tools_payload.items()
            },
        )

    def llm_cost(
        self,
        *,
        provider: Optional[str] = None,
        provider_model_id: Optional[str] = None,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> int:
        rates = self.llm_rates(
            provider=provider,
            provider_model_id=provider_model_id,
        )
        return rates.cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )

    def llm_rates(
        self,
        *,
        provider: Optional[str] = None,
        provider_model_id: Optional[str] = None,
    ) -> LLMCostRates:
        if provider and provider_model_id:
            model_overrides = self.llm_provider_model_overrides.get(provider)
            if model_overrides is not None:
                override = model_overrides.get(provider_model_id)
                if override is not None:
                    return override.resolve(self.llm_default)
        return self.llm_default

    def tool_cost(self, tool_name: str) -> int:
        return self.tools.get(tool_name, 0)


def _parse_provider_model_overrides(
    providers_payload: Mapping[str, Any],
) -> dict[str, dict[str, PartialLLMCostRates]]:
    parsed: dict[str, dict[str, PartialLLMCostRates]] = {}
    for provider_name, provider_payload in providers_payload.items():
        label = f"llm.providers.{provider_name}"
        if not isinstance(provider_payload, Mapping):
            raise ValueError(f"CU tariff response field '{label}' must be a mapping.")
        models_payload = provider_payload.get("models")
        if models_payload is None:
            continue
        if not isinstance(models_payload, Mapping):
            raise ValueError(
                f"CU tariff response field '{label}.models' must be a mapping."
            )
        provider_models: dict[str, PartialLLMCostRates] = {}
        for model_name, rate_payload in models_payload.items():
            provider_models[str(model_name)] = _parse_partial_llm_rates(
                rate_payload,
                label=f"{label}.models.{model_name}",
            )
        parsed[str(provider_name)] = provider_models
    return parsed


def _parse_partial_llm_rates(
    payload: Any,
    *,
    label: str,
) -> PartialLLMCostRates:
    if not isinstance(payload, Mapping):
        raise ValueError(f"CU tariff response field '{label}' must be a mapping.")

    input_cu_per_1k = _optional_int(payload, "input_cu_per_1k", label=label)
    input_cu_per_1m = _optional_int(payload, "input_cu_per_1m", label=label)
    if input_cu_per_1k is not None and input_cu_per_1m is not None:
        raise ValueError(
            f"CU tariff response field '{label}' cannot define both "
            "'input_cu_per_1k' and 'input_cu_per_1m'."
        )

    return PartialLLMCostRates(
        input_cu_per_1k=input_cu_per_1k,
        input_cu_per_1m=input_cu_per_1m,
        output_cu_per_1k=_optional_int(payload, "output_cu_per_1k", label=label),
        cached_cu_per_1k=_optional_int(payload, "cached_cu_per_1k", label=label),
    )


def _ceil_per_1k(tokens: int, rate: int) -> int:
    if tokens <= 0 or rate <= 0:
        return 0
    return (tokens * rate + 999) // 1000


def _ceil_per_1m(tokens: int, rate: int) -> int:
    if tokens <= 0 or rate <= 0:
        return 0
    return (tokens * rate + 999_999) // 1_000_000


def _input_cost(
    *,
    input_tokens: int,
    input_cu_per_1k: Optional[int],
    input_cu_per_1m: Optional[int],
) -> int:
    if input_cu_per_1m is not None:
        return _ceil_per_1m(input_tokens, input_cu_per_1m)
    if input_cu_per_1k is not None:
        return _ceil_per_1k(input_tokens, input_cu_per_1k)
    return 0


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"CU tariff response missing valid '{key}'.")
    return value


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    return _coerce_non_negative_int(value, key)


def _optional_int(
    payload: Mapping[str, Any],
    key: str,
    *,
    label: str,
) -> Optional[int]:
    if key not in payload:
        return None
    return _coerce_non_negative_int(payload.get(key), f"{label}.{key}")


def _coerce_non_negative_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"CU tariff response field '{label}' must be an integer."
        ) from exc
    if parsed < 0:
        raise ValueError(
            f"CU tariff response field '{label}' must be non-negative."
        )
    return parsed
