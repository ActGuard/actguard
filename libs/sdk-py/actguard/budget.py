from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Optional

from actguard.core.state import BudgetState
from actguard.exceptions import BudgetClientMismatchError, NestedBudgetGuardError
from actguard.integrations import patch_all

if TYPE_CHECKING:
    from actguard.client import Client
    from actguard.core.run_context import RunState


class BudgetGuard:
    """Client-bound budget scope layered on top of run scope."""

    def __init__(
        self,
        *,
        client: "Client",
        user_id: Optional[str] = None,
        usd_limit: float,
        run_id: Optional[str] = None,
    ) -> None:
        self._client = client
        self.user_id = user_id
        self.usd_limit = usd_limit
        self._requested_run_id = run_id

        self._state: Optional[BudgetState] = None
        self._run_state: Optional["RunState"] = None
        self._run_context = None
        self._owns_run_context = False
        self.run_id: Optional[str] = None

    def _bind_run_state(self) -> None:
        from actguard.core.run_context import get_run_state, require_run_state

        active = get_run_state()
        if active is not None:
            if (
                self._requested_run_id is not None
                and active.run_id != self._requested_run_id
            ):
                raise ValueError(
                    "budget_guard run_id does not match active runtime run_id."
                )
            if active.budget_state is not None:
                raise NestedBudgetGuardError()
            if active.client is not None and active.client is not self._client:
                raise BudgetClientMismatchError()

            if active.client is None:
                active.client = self._client
            self._run_state = active
            self.run_id = active.run_id
            return

        self._run_context = self._client.run(
            user_id=self.user_id,
            run_id=self._requested_run_id,
        )
        entered = self._run_context.__enter__()
        self._owns_run_context = True
        self.run_id = entered.run_id
        self._run_state = require_run_state()

    def __enter__(self) -> "BudgetGuard":
        if self.usd_limit <= 0:
            raise ValueError("budget_guard requires usd_limit > 0.")

        patch_all()
        self._bind_run_state()
        assert self._run_state is not None

        effective_user_id = self.user_id
        if effective_user_id is None:
            effective_user_id = self._run_state.user_id
        usd_limit_micros = int(round(self.usd_limit * 1_000_000))

        self._state = BudgetState(
            user_id=effective_user_id,
            run_id=self._run_state.run_id,
            usd_limit=self.usd_limit,
            usd_limit_micros=usd_limit_micros,
        )

        try:
            reserve_id = self._client.reserve_budget(
                run_id=self._run_state.run_id,
                usd_limit_micros=usd_limit_micros,
            )
            self._state.reserve_id = reserve_id
            self._run_state.budget_state = self._state
            self._run_state.budget_reservation = {"reserve_id": reserve_id}
        except Exception:
            if self._owns_run_context and self._run_context is not None:
                self._run_context.__exit__(*sys.exc_info())
                self._run_context = None
                self._owns_run_context = False
            self._run_state = None
            self._state = None
            self.run_id = None
            raise

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        settle_error: Optional[Exception] = None

        if self._run_state is not None and self._state is not None:
            try:
                if self._state.reserve_id:
                    self._client.settle_budget(
                        reserve_id=self._state.reserve_id,
                        run_id=self._run_state.run_id,
                        provider=self._state.provider,
                        provider_model_id=self._state.provider_model_id,
                        input_tokens=self._state.input_tokens,
                        cached_input_tokens=self._state.cached_input_tokens,
                        output_tokens=self._state.output_tokens,
                    )
            except Exception as exc:
                settle_error = exc
            finally:
                if self._run_state.budget_state is self._state:
                    self._run_state.budget_state = None
                if self._run_state.budget_reservation is not None:
                    self._run_state.budget_reservation = None

        self._run_state = None

        if self._owns_run_context and self._run_context is not None:
            self._run_context.__exit__(exc_type, exc_val, exc_tb)
            self._run_context = None
            self._owns_run_context = False

        if settle_error is not None and exc_type is None:
            raise settle_error
        return None

    async def __aenter__(self) -> "BudgetGuard":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return self.__exit__(exc_type, exc_val, exc_tb)

    @property
    def tokens_used(self) -> int:
        if self._state is None:
            return 0
        return self._state.tokens_used

    @property
    def usd_used(self) -> float:
        if self._state is None:
            return 0.0
        return self._state.usd_used
