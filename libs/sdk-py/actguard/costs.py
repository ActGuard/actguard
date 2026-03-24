from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class LLMDefaultCostRates:
    input_cu_per_1k: int
    output_cu_per_1k: int
    cached_cu_per_1k: int


@dataclass(frozen=True)
class CuTariff:
    tariff_version: str
    cu_per_usd: int
    registry_version: str
    llm_default: LLMDefaultCostRates
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

        tariff_version = _required_str(payload, "tariff_version")
        registry_version = _required_str(payload, "registry_version")
        return cls(
            tariff_version=tariff_version,
            cu_per_usd=_required_int(payload, "cu_per_usd"),
            registry_version=registry_version,
            llm_default=LLMDefaultCostRates(
                input_cu_per_1k=_required_int(default, "input_cu_per_1k"),
                output_cu_per_1k=_required_int(default, "output_cu_per_1k"),
                cached_cu_per_1k=_required_int(default, "cached_cu_per_1k"),
            ),
            tools={
                str(name): _coerce_non_negative_int(value, f"tools.{name}")
                for name, value in tools_payload.items()
            },
        )

    def llm_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> int:
        return (
            _ceil_per_1k(input_tokens, self.llm_default.input_cu_per_1k)
            + _ceil_per_1k(output_tokens, self.llm_default.output_cu_per_1k)
            + _ceil_per_1k(cached_input_tokens, self.llm_default.cached_cu_per_1k)
        )

    def tool_cost(self, tool_name: str) -> int:
        return self.tools.get(tool_name, 0)


def _ceil_per_1k(tokens: int, rate: int) -> int:
    if tokens <= 0 or rate <= 0:
        return 0
    return (tokens * rate + 999) // 1000


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"CU tariff response missing valid '{key}'.")
    return value


def _required_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    return _coerce_non_negative_int(value, key)


def _coerce_non_negative_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CU tariff response field '{label}' must be an integer.") from exc
    if parsed < 0:
        raise ValueError(
            f"CU tariff response field '{label}' must be non-negative."
        )
    return parsed
