import json
import warnings
from importlib.resources import files
from typing import Any, Dict


def _load_pricing() -> Dict[str, Any]:
    data = files("actguard.data").joinpath("pricing.json").read_text(encoding="utf-8")
    return json.loads(data)


_PRICING: Dict[str, Any] = _load_pricing()


def get_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> float:
    """Return the USD cost for the given token counts.

    Falls back to the ``_default`` entry (cost = 0.0) with a warning if the
    provider/model combination is not found in the pricing table.
    """
    provider_table = _PRICING.get(provider, {})
    entry = provider_table.get(model)

    if entry is None:
        warnings.warn(
            f"actguard: no pricing entry for provider={provider!r} model={model!r}; "
            "cost will be recorded as $0.00. "
            "Please open an issue or PR to add this model.",
            stacklevel=3,
        )
        entry = _PRICING["_default"]

    per_million = 1_000_000
    return (input_tokens * entry["input"] + output_tokens * entry["output"]) / per_million
