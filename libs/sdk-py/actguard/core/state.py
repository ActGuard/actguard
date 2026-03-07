from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BudgetState:
    user_id: Optional[str] = None
    run_id: str = ""
    reserve_id: Optional[str] = None
    provider: str = ""
    provider_model_id: str = ""
    input_tokens: int = field(default=0)
    cached_input_tokens: int = field(default=0)
    output_tokens: int = field(default=0)
    usd_limit: Optional[float] = None
    usd_limit_micros: Optional[int] = None
    tokens_used: int = field(default=0)
    usd_used: float = field(default=0.0)

    def record_usage(
        self,
        *,
        provider: str,
        provider_model_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> None:
        self.provider = provider
        if provider_model_id:
            self.provider_model_id = provider_model_id
        self.input_tokens += input_tokens
        self.cached_input_tokens += cached_input_tokens
        self.output_tokens += output_tokens
        self.tokens_used += input_tokens + output_tokens

def get_current_state() -> Optional[BudgetState]:
    """Return budget state attached to the active runtime run, if any."""
    try:
        from actguard.core.run_context import get_run_state

        run_state = get_run_state()
        if run_state is None:
            return None
        return run_state.budget_state
    except Exception:
        return None
