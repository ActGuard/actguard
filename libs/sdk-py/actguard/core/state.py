from typing import Optional

from actguard.core.budget_context import BudgetState, get_budget_state


def get_current_state() -> Optional[BudgetState]:
    """Compatibility shim for callers still using the Step 1 helper path."""
    return get_budget_state()
