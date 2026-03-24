from __future__ import annotations

from contextvars import Token
from typing import TYPE_CHECKING, Optional

from actguard._monitoring import warn_monitoring_issue
from actguard.core.budget_context import (
    BudgetState,
    SharedBudgetState,
    build_root_scope_state,
    check_budget_limits,
    get_budget_stack,
    pop_budget_scope,
    push_budget_scope,
    record_usage,
)
from actguard.core.budget_recorder import (
    reset_current_budget_recorder,
    set_current_budget_recorder,
)
from actguard.exceptions import (
    ActGuardPaymentRequired,
    ActGuardRuntimeContextError,
    ActGuardUsageError,
    BudgetClientMismatchError,
    BudgetConfigurationError,
    BudgetExceededError,
)

if TYPE_CHECKING:
    from actguard.client import Client
    from actguard.core.run_context import RunState


class _EagerBudgetRecorder:
    """BudgetRecorder that delegates to global budget_context helpers immediately."""

    def record_usage(
        self,
        *,
        provider: str,
        provider_model_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> None:
        record_usage(
            provider=provider,
            provider_model_id=provider_model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        )

    def check_limits(self) -> None:
        from actguard.budget_events import emit_budget_blocked

        violation = check_budget_limits()
        if violation is not None:
            blocked_scope = violation.blocked_scope
            emit_budget_blocked(blocked_scope)
            raise BudgetExceededError(
                user_id=blocked_scope.user_id,
                tokens_used=blocked_scope.tokens_used,
                cost_used=blocked_scope.cost_used,
                cost_limit=blocked_scope.cost_limit,
                limit_type="cost",
                scope_id=blocked_scope.scope_id,
                scope_name=blocked_scope.scope_name,
                scope_kind=blocked_scope.scope_kind,
                parent_scope_id=blocked_scope.parent_scope_id,
                root_scope_id=blocked_scope.root_scope_id,
            )

class BudgetGuard:
    """Client-bound budget scope layered on top of run scope."""

    def __init__(
        self,
        *,
        client: "Client",
        user_id: Optional[str] = None,
        name: Optional[str] = None,
        cost_limit: Optional[int] = None,
        run_id: Optional[str] = None,
        plan_key: Optional[str] = None,
    ) -> None:
        self._client = client
        self.user_id = user_id
        self.name = name
        self.cost_limit = cost_limit
        self._requested_run_id = run_id
        self.plan_key = plan_key or None

        self._state: Optional[BudgetState] = None
        self._shared_root: Optional[SharedBudgetState] = None
        self._budget_token: Optional[Token] = None
        self._recorder_token: Optional[Token] = None
        self._run_state: Optional["RunState"] = None
        self._run_id: Optional[str] = None
        self._is_root_scope = False
        self._created_root = False
        self.run_id: Optional[str] = None

    def _budget_reporting_enabled(self) -> bool:
        config = self._client.reporting_config
        return bool(config.gateway_url and config.api_key)

    def _warn_budget_transport_issue(
        self,
        *,
        operation: str,
        exc: BaseException,
    ) -> None:
        warn_monitoring_issue(
            subsystem="budget",
            operation=operation,
            exc=exc,
            path=f"/api/v1/{operation}",
            stacklevel=3,
        )

    @staticmethod
    def _should_reraise_budget_enforcement(exc: BaseException) -> bool:
        return isinstance(exc, (BudgetExceededError, ActGuardPaymentRequired))

    def _bind_run_state(self) -> Optional[str]:
        from actguard.core.run_context import require_run_state

        active = require_run_state()
        if (
            self._requested_run_id is not None
            and active.run_id != self._requested_run_id
        ):
            raise ActGuardRuntimeContextError(
                "budget_guard run_id does not match active runtime run_id.",
                code="runtime.run_id_mismatch",
                reason="budget_run_id_mismatch",
                retryable=False,
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
        if (
            self.cost_limit is not None
            and shared_root.root_cost_limit != self.cost_limit
        ):
            raise BudgetConfigurationError(
                "budget_guard cost_limit does not match the existing root scope."
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
            cost_limit=self.cost_limit,
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
            cost_limit=self.cost_limit,
            plan_key=shared_root.plan_key,
            reserve_id=shared_root.reserve_id,
            tariff=shared_root.tariff,
            tariff_version=shared_root.tariff_version,
            shared_root=shared_root,
        )

    def _rollback_root_attach(self) -> None:
        if self._shared_root is None or self._run_state is None:
            return
        self._run_state.release_budget_root(self._shared_root)

    def _cost_enforcement_requested(self) -> bool:
        return bool(
            (self._state is not None and self._state.cost_limit is not None)
            or (
                self._shared_root is not None
                and self._shared_root.root_cost_limit is not None
            )
        )

    def _install_tariff(self, tariff) -> None:
        if self._shared_root is None or self._state is None:
            return
        self._shared_root.install_tariff(tariff)
        self._state.tariff = tariff
        self._state.tariff_version = tariff.tariff_version

    def _ensure_tariff(self) -> None:
        if self._shared_root is None or self._state is None:
            return
        if self._shared_root.tariff is not None:
            self._state.tariff = self._shared_root.tariff
            self._state.tariff_version = self._shared_root.tariff_version
            return
        tariff = self._client.get_cu_tariff()
        self._install_tariff(tariff)

    def __enter__(self) -> "BudgetGuard":
        if self.cost_limit is not None and (
            not isinstance(self.cost_limit, int)
            or isinstance(self.cost_limit, bool)
            or self.cost_limit <= 0
        ):
            raise ActGuardUsageError(
                (
                    "budget_guard requires cost_limit to be a positive integer "
                    "when provided."
                ),
                code="usage.budget_guard_configuration",
                reason="budget_guard_configuration",
                retryable=False,
            )

        active_user_id = self._bind_run_state()
        self._client.prepare_budget_scope()
        assert self._run_id is not None

        effective_user_id = self.user_id if self.user_id is not None else active_user_id

        if get_budget_stack():
            scope = self._attach_nested_scope(user_id=effective_user_id)
        else:
            scope = self._attach_root_scope(user_id=effective_user_id)

        self._state = scope

        try:
            if self._cost_enforcement_requested():
                try:
                    self._ensure_tariff()
                except Exception as exc:
                    self._warn_budget_transport_issue(
                        operation="cu-tariff",
                        exc=exc,
                    )
            if self._is_root_scope and self._created_root:
                assert self._shared_root is not None
                if self._budget_reporting_enabled():
                    try:
                        reserve_response = self._client.reserve_budget(
                            run_id=self._run_id,
                            cost_limit=self._shared_root.root_cost_limit,
                            plan_key=self._shared_root.plan_key,
                            user_id=self._shared_root.user_id,
                        )
                    except Exception as exc:
                        if self._should_reraise_budget_enforcement(exc):
                            raise
                        self._warn_budget_transport_issue(
                            operation="reserve",
                            exc=exc,
                        )
                    else:
                        if isinstance(reserve_response, str):
                            reserve_id = reserve_response
                            reserve_metadata = {}
                        else:
                            reserve_id = reserve_response["reserve_id"]
                            reserve_metadata = reserve_response
                        self._shared_root.reserve_id = reserve_id
                        self._state.reserve_id = reserve_id
                        response_cost_limit = reserve_metadata.get("cost_limit")
                        if (
                            isinstance(response_cost_limit, int)
                            and response_cost_limit > 0
                            and self._shared_root.root_cost_limit is None
                        ):
                            self._shared_root.root_cost_limit = response_cost_limit
                            self._state.cost_limit = response_cost_limit
                        response_tariff_version = reserve_metadata.get(
                            "tariff_version"
                        )
                        if (
                            isinstance(response_tariff_version, str)
                            and response_tariff_version
                        ):
                            self._shared_root.tariff_version = response_tariff_version
                            self._state.tariff_version = response_tariff_version
                        estimated_usd_micros = reserve_metadata.get(
                            "estimated_usd_micros"
                        )
                        if isinstance(estimated_usd_micros, int):
                            self._shared_root.estimated_usd_micros = (
                                estimated_usd_micros
                            )

            self._budget_token = push_budget_scope(
                scope,
                inherit_active_source=not self._is_root_scope,
            )
            self._recorder_token = set_current_budget_recorder(
                _EagerBudgetRecorder()
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
        if self._recorder_token is not None:
            reset_current_budget_recorder(self._recorder_token)
            self._recorder_token = None

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
                            shared_root.provider_model_id = (
                                self._state.provider_model_id
                            )
                            shared_root.input_tokens = self._state.input_tokens
                            shared_root.cached_input_tokens = (
                                self._state.cached_input_tokens
                            )
                            shared_root.output_tokens = self._state.output_tokens
                            shared_root.tokens_used = self._state.tokens_used
                            shared_root.cost_used = self._state.cost_used
                        usage_breakdown = shared_root.usage_breakdown_payload()
                        if not usage_breakdown and self._state is not None:
                            has_fallback_usage = (
                                self._state.input_tokens
                                + self._state.cached_input_tokens
                                + self._state.output_tokens
                            ) > 0
                            if has_fallback_usage:
                                usage_breakdown = [
                                    {
                                        "provider": self._state.provider,
                                        "provider_model_id": (
                                            self._state.provider_model_id
                                        ),
                                        "input_tokens": self._state.input_tokens,
                                        "cached_input_tokens": (
                                            self._state.cached_input_tokens
                                        ),
                                        "output_tokens": self._state.output_tokens,
                                    }
                                ]
                                if self._state.scope_name:
                                    usage_breakdown[0]["scope_name"] = (
                                        self._state.scope_name
                                    )
                        self._client.settle_budget(
                            reserve_id=shared_root.reserve_id,
                            input_tokens=shared_root.input_tokens,
                            cached_input_tokens=shared_root.cached_input_tokens,
                            output_tokens=shared_root.output_tokens,
                            usage_breakdown=usage_breakdown,
                        )
                except Exception as exc:
                    if self._should_reraise_budget_enforcement(exc):
                        raise
                    self._warn_budget_transport_issue(
                        operation="settle",
                        exc=exc,
                    )

        self._run_state = None
        self._run_id = None

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
    def local_cost_used(self) -> int:
        if self._state is None:
            return 0
        return self._state.cost_used

    @property
    def root_tokens_used(self) -> int:
        if self._shared_root is not None:
            return self._shared_root.tokens_used
        if self._state is None:
            return 0
        return self._state.root_totals()[0]

    @property
    def root_cost_used(self) -> int:
        if self._shared_root is not None:
            return self._shared_root.cost_used
        if self._state is None:
            return 0
        return self._state.root_totals()[1]

    @property
    def tokens_used(self) -> int:
        if self._state is None:
            return 0
        if self._state.scope_kind == "root":
            return self.root_tokens_used
        return self.local_tokens_used

    @property
    def cost_used(self) -> int:
        if self._state is None:
            return 0
        if self._state.scope_kind == "root":
            return self.root_cost_used
        return self.local_cost_used

    @property
    def local_usd_used(self) -> float:
        if self._state is None:
            return 0.0
        cu_per_usd = None
        if self._shared_root is not None:
            cu_per_usd = self._shared_root.cu_per_usd
        if not cu_per_usd:
            return 0.0
        return self.local_cost_used / cu_per_usd

    @property
    def root_usd_used(self) -> float:
        if self._state is None:
            return 0.0
        cu_per_usd = None
        if self._shared_root is not None:
            cu_per_usd = self._shared_root.cu_per_usd
        if not cu_per_usd:
            return 0.0
        return self.root_cost_used / cu_per_usd

    @property
    def usd_used(self) -> float:
        if self._state is None:
            return 0.0
        if self._state.scope_kind == "root":
            return self.root_usd_used
        return self.local_usd_used
