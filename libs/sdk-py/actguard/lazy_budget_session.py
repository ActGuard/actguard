"""Lazy request-scoped budget session that defers reservation until first usage."""
from __future__ import annotations

from contextvars import Token
from threading import Lock
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
    ActGuardRuntimeContextError,
    ActGuardUsageError,
    BudgetClientMismatchError,
    BudgetConfigurationError,
    BudgetExceededError,
)

if TYPE_CHECKING:
    from actguard.client import Client
    from actguard.core.run_context import RunState


def _to_micros(usd_limit: Optional[float]) -> Optional[int]:
    if usd_limit is None:
        return None
    return int(round(usd_limit * 1_000_000))


class LazyRequestBudgetSession:
    """Budget session that defers ``reserve_budget()`` until first metered usage.

    Requests with no LLM calls skip reserve/settle entirely.
    """

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
        self._recorder_token: Optional[Token] = None
        self._run_state: Optional["RunState"] = None
        self._run_id: Optional[str] = None
        self._is_root_scope = False
        self._created_root = False
        self.run_id: Optional[str] = None

        # Lazy reservation state
        self._reserve_lock = Lock()
        self._record_lock = Lock()  # covers full record_usage() flow
        self._reserved = False
        self._reserve_failed = False

    def _budget_reporting_enabled(self) -> bool:
        config = self._client.reporting_config
        return bool(config.gateway_url and config.api_key)

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
            self.usd_limit is not None
            and shared_root.root_budget_limit != self.usd_limit
        ):
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

    def _rollback_root_attach(self) -> None:
        if self._shared_root is None or self._run_state is None:
            return
        self._run_state.release_budget_root(self._shared_root)

    def _ensure_reserved(self) -> None:
        """Thread-safe lazy reservation. Called on first metered usage."""
        if self._reserved or self._reserve_failed:
            return
        with self._reserve_lock:
            if self._reserved or self._reserve_failed:
                return
            if not (self._is_root_scope and self._created_root):
                self._reserved = True
                return
            if not self._budget_reporting_enabled():
                self._reserved = True
                return
            assert self._shared_root is not None
            assert self._run_id is not None
            try:
                reserve_id = self._client.reserve_budget(
                    run_id=self._run_id,
                    usd_limit_micros=self._shared_root.root_budget_limit_micros,
                    plan_key=self._shared_root.plan_key,
                    user_id=self._shared_root.user_id,
                )
            except Exception as exc:
                warn_monitoring_issue(
                    subsystem="budget",
                    operation="reserve",
                    exc=exc,
                    path="/api/v1/reserve",
                    stacklevel=3,
                )
                self._reserve_failed = True
            else:
                self._shared_root.reserve_id = reserve_id
                if self._state is not None:
                    self._state.reserve_id = reserve_id
                self._reserved = True

    # -- BudgetRecorder interface --

    def record_usage(
        self,
        *,
        provider: str,
        provider_model_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> None:
        with self._record_lock:
            self._ensure_reserved()
            record_usage(
                provider=provider,
                provider_model_id=provider_model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
            )

    def check_limits(self) -> None:
        violation = check_budget_limits()
        if violation is not None:
            from actguard.budget_events import emit_budget_blocked

            blocked_scope = violation.blocked_scope
            emit_budget_blocked(blocked_scope)
            raise BudgetExceededError(
                user_id=blocked_scope.user_id,
                tokens_used=blocked_scope.tokens_used,
                usd_used=blocked_scope.usd_used,
                usd_limit=blocked_scope.usd_limit,
                limit_type="usd",
                scope_id=blocked_scope.scope_id,
                scope_name=blocked_scope.scope_name,
                scope_kind=blocked_scope.scope_kind,
                parent_scope_id=blocked_scope.parent_scope_id,
                root_scope_id=blocked_scope.root_scope_id,
            )

    # -- Context manager --

    def __enter__(self) -> "LazyRequestBudgetSession":
        if self.usd_limit is not None and self.usd_limit <= 0:
            raise ActGuardUsageError(
                "budget_guard requires usd_limit > 0 when provided.",
                code="usage.budget_guard_configuration",
                reason="budget_guard_configuration",
                retryable=False,
            )

        active_user_id = self._bind_run_state()
        self._client.prepare_budget_scope()
        assert self._run_id is not None

        effective_user_id = self.user_id if self.user_id is not None else active_user_id

        if get_budget_stack():
            raise ActGuardUsageError(
                "LazyRequestBudgetSession does not support "
                "nesting inside an existing budget scope.",
                code="usage.budget_guard_configuration",
                reason="budget_guard_configuration",
                retryable=False,
            )

        scope = self._attach_root_scope(user_id=effective_user_id)
        self._state = scope

        try:
            self._budget_token = push_budget_scope(
                scope,
                inherit_active_source=False,
            )
            self._recorder_token = set_current_budget_recorder(self)
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
        # --- settle logic FIRST (recorder + scope still active for workers) ---
        if self._is_root_scope and self._shared_root is not None:
            shared_root = self._shared_root
            assert self._run_state is not None
            should_settle = self._run_state.release_budget_root(shared_root)

            # Wait for any in-progress record_usage() from worker threads
            with self._record_lock:
                pass

            # Safety net: if usage arrived via the SharedBudgetState fallback
            # (e.g. worker threads that bypassed the recorder ContextVar),
            # trigger a late reserve so settle can proceed.
            if (
                should_settle
                and not self._reserved
                and not self._reserve_failed
                and (shared_root.input_tokens + shared_root.output_tokens) > 0
            ):
                self._ensure_reserved()

            if should_settle and self._reserved and not self._reserve_failed:
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
                            shared_root.usd_used = self._state.usd_used
                        has_usage = (
                            shared_root.input_tokens + shared_root.output_tokens
                        ) > 0
                        self._client.settle_budget(
                            reserve_id=shared_root.reserve_id,
                            provider=shared_root.provider
                            or ("none" if not has_usage else ""),
                            provider_model_id=(
                                shared_root.provider_model_id
                                or ("none" if not has_usage else "")
                            ),
                            input_tokens=shared_root.input_tokens,
                            cached_input_tokens=shared_root.cached_input_tokens,
                            output_tokens=shared_root.output_tokens,
                        )
                except Exception as exc:
                    warn_monitoring_issue(
                        subsystem="budget",
                        operation="settle",
                        exc=exc,
                        path="/api/v1/settle",
                        stacklevel=3,
                    )

        # --- THEN cleanup recorder + scope ---
        if self._recorder_token is not None:
            reset_current_budget_recorder(self._recorder_token)
            self._recorder_token = None

        if self._budget_token is not None:
            pop_budget_scope(self._budget_token)
            self._budget_token = None

        self._run_state = None
        self._run_id = None

        return None

    async def __aenter__(self) -> "LazyRequestBudgetSession":
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
