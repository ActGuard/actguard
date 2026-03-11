from __future__ import annotations

from contextvars import Token
from typing import TYPE_CHECKING, Optional

from actguard.core.budget_context import (
    BudgetState,
    SharedBudgetState,
    build_root_scope_state,
    get_budget_stack,
    pop_budget_scope,
    push_budget_scope,
)
from actguard.exceptions import (
    BudgetClientMismatchError,
    BudgetConfigurationError,
)
from actguard.integrations import patch_all

if TYPE_CHECKING:
    from actguard.client import Client
    from actguard.core.run_context import RunState


def _to_micros(usd_limit: Optional[float]) -> Optional[int]:
    if usd_limit is None:
        return None
    return int(round(usd_limit * 1_000_000))


class BudgetGuard:
    """Client-bound budget scope layered on top of run scope."""

    def __init__(
        self,
        *,
        client: "Client",
        user_id: Optional[str] = None,
        name: Optional[str] = None,
        usd_limit: Optional[float] = None,
        run_id: Optional[str] = None,
        plan_key: Optional[str] = None,
    ) -> None:
        self._client = client
        self.user_id = user_id
        self.name = name
        self.usd_limit = usd_limit
        self._requested_run_id = run_id
        self.plan_key = plan_key or None

        self._state: Optional[BudgetState] = None
        self._shared_root: Optional[SharedBudgetState] = None
        self._budget_token: Optional[Token] = None
        self._run_state: Optional["RunState"] = None
        self._run_id: Optional[str] = None
        self._is_root_scope = False
        self._created_root = False
        self.run_id: Optional[str] = None

    def _bind_run_state(self) -> Optional[str]:
        from actguard.core.run_context import require_run_state

        active = require_run_state()
        if (
            self._requested_run_id is not None
            and active.run_id != self._requested_run_id
        ):
            raise ValueError(
                "budget_guard run_id does not match active runtime run_id."
            )
        if active.client is not None and active.client is not self._client:
            raise BudgetClientMismatchError()

        if active.client is None:
            active.client = self._client

        self._run_state = active
        self._run_id = active.run_id
        self.run_id = active.run_id
        return active.user_id

    def _validate_root_config(
        self,
        shared_root: SharedBudgetState,
        *,
        user_id: Optional[str],
    ) -> None:
        if user_id is not None and shared_root.user_id != user_id:
            raise BudgetConfigurationError(
                "budget_guard user_id does not match the existing root scope."
            )
        if self.name is not None and shared_root.root_scope_name != self.name:
            raise BudgetConfigurationError(
                "budget_guard name does not match the existing root scope."
            )
        if self.usd_limit is not None and shared_root.root_budget_limit != self.usd_limit:
            raise BudgetConfigurationError(
                "budget_guard usd_limit does not match the existing root scope."
            )
        if self.plan_key is not None and shared_root.plan_key != self.plan_key:
            raise BudgetConfigurationError(
                "budget_guard plan_key does not match the existing root scope."
            )

    def _attach_root_scope(self, *, user_id: Optional[str]) -> BudgetState:
        assert self._run_state is not None
        shared_root, created = self._run_state.acquire_budget_root(
            user_id=user_id,
            scope_name=self.name,
            usd_limit=self.usd_limit,
            usd_limit_micros=_to_micros(self.usd_limit),
            plan_key=self.plan_key,
        )
        if not created:
            self._validate_root_config(shared_root, user_id=user_id)

        self._shared_root = shared_root
        self._created_root = created
        self._is_root_scope = True
        return build_root_scope_state(shared_root)

    def _attach_nested_scope(self, *, user_id: Optional[str]) -> BudgetState:
        stack = get_budget_stack()
        parent_scope = stack[-1]
        shared_root = parent_scope.shared_root
        assert shared_root is not None
        if self.plan_key is not None and shared_root.plan_key != self.plan_key:
            raise BudgetConfigurationError(
                "budget_guard plan_key does not match the existing root scope."
            )

        self._shared_root = shared_root
        self._is_root_scope = False
        return BudgetState(
            user_id=user_id,
            run_id=parent_scope.run_id,
            tenant_id=parent_scope.tenant_id,
            scope_name=self.name,
            scope_kind="nested",
            parent_scope_id=parent_scope.scope_id,
            root_scope_id=shared_root.root_scope_id,
            usd_limit=self.usd_limit,
            usd_limit_micros=_to_micros(self.usd_limit),
            plan_key=shared_root.plan_key,
            reserve_id=shared_root.reserve_id,
            shared_root=shared_root,
        )

    def _rollback_root_attach(self) -> None:
        if self._shared_root is None or self._run_state is None:
            return
        self._run_state.release_budget_root(self._shared_root)

    def __enter__(self) -> "BudgetGuard":
        if self.usd_limit is not None and self.usd_limit <= 0:
            raise ValueError("budget_guard requires usd_limit > 0 when provided.")

        active_user_id = self._bind_run_state()
        patch_all()
        assert self._run_id is not None

        effective_user_id = self.user_id if self.user_id is not None else active_user_id

        if get_budget_stack():
            scope = self._attach_nested_scope(user_id=effective_user_id)
        else:
            scope = self._attach_root_scope(user_id=effective_user_id)

        self._state = scope

        try:
            if self._is_root_scope and self._created_root:
                assert self._shared_root is not None
                reserve_id = self._client.reserve_budget(
                    run_id=self._run_id,
                    usd_limit_micros=self._shared_root.root_budget_limit_micros,
                    plan_key=self._shared_root.plan_key,
                    user_id=self._shared_root.user_id,
                )
                self._shared_root.reserve_id = reserve_id
                self._state.reserve_id = reserve_id

            self._budget_token = push_budget_scope(
                scope,
                inherit_active_source=not self._is_root_scope,
            )
        except Exception:
            if self._is_root_scope:
                self._rollback_root_attach()
            self._state = None
            self._shared_root = None
            self._run_state = None
            self._run_id = None
            self.run_id = None
            raise

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        settle_error: Optional[Exception] = None

        if self._budget_token is not None:
            pop_budget_scope(self._budget_token)
            self._budget_token = None

        if self._is_root_scope and self._shared_root is not None:
            shared_root = self._shared_root
            assert self._run_state is not None
            should_settle = self._run_state.release_budget_root(shared_root)
            if should_settle:
                try:
                    if shared_root.reserve_id and shared_root.mark_settled():
                        if self._state is not None and shared_root.tokens_used == 0:
                            shared_root.provider = self._state.provider
                            shared_root.provider_model_id = self._state.provider_model_id
                            shared_root.input_tokens = self._state.input_tokens
                            shared_root.cached_input_tokens = self._state.cached_input_tokens
                            shared_root.output_tokens = self._state.output_tokens
                            shared_root.tokens_used = self._state.tokens_used
                            shared_root.usd_used = self._state.usd_used
                        has_usage = (shared_root.input_tokens + shared_root.output_tokens) > 0
                        self._client.settle_budget(
                            reserve_id=shared_root.reserve_id,
                            provider=shared_root.provider or ("none" if not has_usage else ""),
                            provider_model_id=(
                                shared_root.provider_model_id
                                or ("none" if not has_usage else "")
                            ),
                            input_tokens=shared_root.input_tokens,
                            cached_input_tokens=shared_root.cached_input_tokens,
                            output_tokens=shared_root.output_tokens,
                        )
                except Exception as exc:
                    settle_error = exc

        self._run_state = None
        self._run_id = None

        if settle_error is not None and exc_type is None:
            raise settle_error
        return None

    async def __aenter__(self) -> "BudgetGuard":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return self.__exit__(exc_type, exc_val, exc_tb)

    @property
    def local_tokens_used(self) -> int:
        if self._state is None:
            return 0
        return self._state.tokens_used

    @property
    def local_usd_used(self) -> float:
        if self._state is None:
            return 0.0
        return self._state.usd_used

    @property
    def root_tokens_used(self) -> int:
        if self._shared_root is not None:
            return self._shared_root.tokens_used
        if self._state is None:
            return 0
        return self._state.root_totals()[0]

    @property
    def root_usd_used(self) -> float:
        if self._shared_root is not None:
            return self._shared_root.usd_used
        if self._state is None:
            return 0.0
        return self._state.root_totals()[1]

    @property
    def tokens_used(self) -> int:
        if self._state is None:
            return 0
        if self._state.scope_kind == "root":
            return self.root_tokens_used
        return self.local_tokens_used

    @property
    def usd_used(self) -> float:
        if self._state is None:
            return 0.0
        if self._state.scope_kind == "root":
            return self.root_usd_used
        return self.local_usd_used
