from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BudgetState:
    user_id: str
    token_limit: Optional[int]
    usd_limit: Optional[float]
    tokens_used: int = field(default=0)
    usd_used: float = field(default=0.0)


_budget_state: ContextVar[Optional[BudgetState]] = ContextVar(
    "_budget_state", default=None
)


def get_current_state() -> Optional[BudgetState]:
    return _budget_state.get()


def set_state(state: BudgetState) -> Token:
    return _budget_state.set(state)


def reset_state(token: Token) -> None:
    _budget_state.reset(token)
