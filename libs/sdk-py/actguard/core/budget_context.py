from __future__ import annotations

import asyncio
import threading
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, Literal, Optional

from actguard.costs import CuTariff

ScopeKind = Literal["root", "nested"]


def _current_execution_key() -> tuple[int, Optional[int]]:
    task_id: Optional[int] = None
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is not None:
        task_id = id(task)
    return threading.get_ident(), task_id


@dataclass
class UsageBreakdownEntry:
    provider: str
    provider_model_id: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    scope_name: Optional[str] = None

    def as_payload(self) -> dict:
        payload = {
            "provider": self.provider,
            "provider_model_id": self.provider_model_id,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
        }
        if self.scope_name:
            payload["scope_name"] = self.scope_name
        return payload


@dataclass
class SharedBudgetState:
    user_id: Optional[str]
    run_id: str
    tenant_id: str = ""
    root_scope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    root_scope_name: Optional[str] = None
    root_cost_limit: Optional[int] = None
    plan_key: Optional[str] = None
    reserve_id: Optional[str] = None
    tariff: Optional[CuTariff] = None
    tariff_version: Optional[str] = None
    registry_version: Optional[str] = None
    cu_per_usd: Optional[int] = None
    estimated_usd_micros: Optional[int] = None
    provider: str = ""
    provider_model_id: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    tokens_used: int = 0
    cost_used: int = 0
    usage_breakdown: list[UsageBreakdownEntry] = field(default_factory=list)
    _attachments: int = 0
    _settled: bool = False
    _lock: Lock = field(default_factory=Lock)

    def attach(self) -> None:
        with self._lock:
            self._attachments += 1

    def detach(self) -> bool:
        with self._lock:
            if self._attachments > 0:
                self._attachments -= 1
            return self._attachments == 0

    def record_usage(
        self,
        *,
        provider: str,
        provider_model_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        scope_name: Optional[str] = None,
    ) -> None:
        with self._lock:
            self.provider = provider
            if provider_model_id:
                self.provider_model_id = provider_model_id
            self.input_tokens += input_tokens
            self.cached_input_tokens += cached_input_tokens
            self.output_tokens += output_tokens
            self.tokens_used += input_tokens + output_tokens
            if self.tariff is not None:
                self.cost_used += self.tariff.llm_cost(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                )
            self.usage_breakdown.append(
                UsageBreakdownEntry(
                    provider=provider,
                    provider_model_id=provider_model_id,
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_input_tokens,
                    output_tokens=output_tokens,
                    scope_name=scope_name,
                )
            )

    def usage_breakdown_payload(self) -> list[dict]:
        with self._lock:
            return [entry.as_payload() for entry in self.usage_breakdown]

    def install_tariff(self, tariff: CuTariff) -> None:
        with self._lock:
            self.tariff = tariff
            self.tariff_version = tariff.tariff_version
            self.registry_version = tariff.registry_version
            self.cu_per_usd = tariff.cu_per_usd

    def mark_settled(self) -> bool:
        with self._lock:
            if self._settled:
                return False
            self._settled = True
            return True


@dataclass
class BudgetState:
    user_id: Optional[str] = None
    run_id: str = ""
    tenant_id: str = ""
    scope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    scope_name: Optional[str] = None
    scope_kind: ScopeKind = "root"
    parent_scope_id: Optional[str] = None
    root_scope_id: str = ""
    cost_limit: Optional[int] = None
    usd_limit: Optional[float] = None
    usd_limit_micros: Optional[int] = None
    plan_key: Optional[str] = None
    reserve_id: Optional[str] = None
    tariff: Optional[CuTariff] = None
    tariff_version: Optional[str] = None
    provider: str = ""
    provider_model_id: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    tokens_used: int = 0
    cost_used: int = 0
    shared_root: Optional[SharedBudgetState] = None
    _lock: Lock = field(default_factory=Lock)

    def __post_init__(self) -> None:
        if not self.root_scope_id:
            self.root_scope_id = self.scope_id

    def record_usage(
        self,
        *,
        provider: str,
        provider_model_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> None:
        with self._lock:
            self.provider = provider
            if provider_model_id:
                self.provider_model_id = provider_model_id
            self.input_tokens += input_tokens
            self.cached_input_tokens += cached_input_tokens
            self.output_tokens += output_tokens
            self.tokens_used += input_tokens + output_tokens
            tariff = self.tariff
            if tariff is None and self.shared_root is not None:
                tariff = self.shared_root.tariff
            if tariff is not None:
                self.cost_used += tariff.llm_cost(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                )

    def scope_metadata(self) -> dict:
        metadata = {
            "scope_id": self.scope_id,
            "scope_name": self.scope_name,
            "scope_kind": self.scope_kind,
            "parent_scope_id": self.parent_scope_id,
            "root_scope_id": self.root_scope_id,
        }
        if self.plan_key:
            metadata["plan_key"] = self.plan_key
        return metadata

    def root_totals(self) -> tuple[int, int]:
        if self.shared_root is None:
            return self.tokens_used, self.cost_used
        return self.shared_root.tokens_used, self.shared_root.cost_used


@dataclass
class BudgetExecutionContext:
    shared_root: SharedBudgetState
    scope_stack: list[BudgetState]
    owner_key: tuple[int, Optional[int]] = field(default_factory=_current_execution_key)

    def active_scope(self) -> Optional[BudgetState]:
        if not self.scope_stack:
            return None
        return self.scope_stack[-1]

    def clone_for_current_path(self) -> "BudgetExecutionContext":
        cloned_stack = [
            BudgetState(
                user_id=scope.user_id,
                run_id=scope.run_id,
                tenant_id=scope.tenant_id,
                scope_id=scope.scope_id,
                scope_name=scope.scope_name,
                scope_kind=scope.scope_kind,
                parent_scope_id=scope.parent_scope_id,
                root_scope_id=scope.root_scope_id,
                cost_limit=scope.cost_limit,
                plan_key=scope.plan_key,
                reserve_id=scope.reserve_id,
                tariff=scope.tariff,
                tariff_version=scope.tariff_version,
                provider=scope.provider,
                provider_model_id=scope.provider_model_id,
                input_tokens=scope.input_tokens,
                cached_input_tokens=scope.cached_input_tokens,
                output_tokens=scope.output_tokens,
                tokens_used=scope.tokens_used,
                cost_used=scope.cost_used,
                shared_root=self.shared_root,
            )
            for scope in self.scope_stack
        ]
        return BudgetExecutionContext(
            shared_root=self.shared_root,
            scope_stack=cloned_stack,
            owner_key=_current_execution_key(),
        )


@dataclass(frozen=True)
class BudgetLimitViolation:
    active_scope: BudgetState
    blocked_scope: BudgetState


_budget_context: ContextVar[Optional[BudgetExecutionContext]] = ContextVar(
    "_budget_context", default=None
)

# Fallback registry for worker threads that do not inherit ContextVar state.
_active_budget_states: Dict[int, BudgetState] = {}
_active_budget_contexts: Dict[int, BudgetExecutionContext] = {}
_active_budget_states_lock: Lock = Lock()


def _register_budget_context(context: BudgetExecutionContext) -> None:
    with _active_budget_states_lock:
        _active_budget_contexts[id(context)] = context
        active_scope = context.active_scope()
        if active_scope is not None:
            _active_budget_states[id(active_scope)] = active_scope


def _unregister_budget_context(context: Optional[BudgetExecutionContext]) -> None:
    if context is None:
        return
    with _active_budget_states_lock:
        _active_budget_contexts.pop(id(context), None)
        active_scope = context.active_scope()
        if active_scope is not None:
            _active_budget_states.pop(id(active_scope), None)


def _sole_active_budget_context() -> Optional[BudgetExecutionContext]:
    # In async contexts, ContextVars are authoritative — skip fallback registry
    from actguard.core.run_context import _in_async_context
    if _in_async_context():
        return None
    with _active_budget_states_lock:
        if len(_active_budget_contexts) != 1:
            return None
        return next(iter(_active_budget_contexts.values()))


def _ensure_execution_context() -> Optional[BudgetExecutionContext]:
    context = _budget_context.get()
    if context is None:
        return None
    owner_key = _current_execution_key()
    if context.owner_key == owner_key:
        return context

    cloned = context.clone_for_current_path()
    _budget_context.set(cloned)
    _register_budget_context(cloned)
    return cloned


def get_budget_context() -> Optional[BudgetExecutionContext]:
    return _ensure_execution_context()


def get_budget_stack() -> tuple[BudgetState, ...]:
    context = _ensure_execution_context()
    if context is None:
        return ()
    return tuple(context.scope_stack)


def get_budget_state() -> Optional[BudgetState]:
    context = _ensure_execution_context()
    if context is not None:
        return context.active_scope()
    # Fallback only for worker threads — async tasks have proper ContextVar isolation
    from actguard.core.run_context import _in_async_context
    if _in_async_context():
        return None
    with _active_budget_states_lock:
        if len(_active_budget_states) == 1:
            return next(iter(_active_budget_states.values()))
    return None


def set_budget_state(state: BudgetState) -> Token:
    shared_root = state.shared_root
    if shared_root is None:
        shared_root = SharedBudgetState(
            user_id=state.user_id,
            run_id=state.run_id,
            tenant_id=state.tenant_id,
            root_scope_id=state.root_scope_id or state.scope_id,
            root_scope_name=state.scope_name,
            root_cost_limit=state.cost_limit,
            plan_key=state.plan_key,
            reserve_id=state.reserve_id,
            tariff=state.tariff,
            tariff_version=state.tariff_version,
            provider=state.provider,
            provider_model_id=state.provider_model_id,
            input_tokens=state.input_tokens,
            cached_input_tokens=state.cached_input_tokens,
            output_tokens=state.output_tokens,
            tokens_used=state.tokens_used,
            cost_used=state.cost_used,
        )
        state.shared_root = shared_root
    context = BudgetExecutionContext(
        shared_root=shared_root,
        scope_stack=[state],
    )
    token = _budget_context.set(context)
    _register_budget_context(context)
    return token


def reset_budget_state(token: Token) -> None:
    context = _budget_context.get()
    _unregister_budget_context(context)
    _budget_context.reset(token)
    restored = _budget_context.get()
    if restored is not None:
        if restored.owner_key != _current_execution_key():
            restored = restored.clone_for_current_path()
            _budget_context.set(restored)
        _register_budget_context(restored)


def install_budget_context(context: BudgetExecutionContext) -> Token:
    previous = _ensure_execution_context()
    _unregister_budget_context(previous)
    token = _budget_context.set(context)
    _register_budget_context(context)
    return token


def reset_budget_context(token: Token) -> None:
    reset_budget_state(token)


def build_root_scope_state(shared_root: SharedBudgetState) -> BudgetState:
    return BudgetState(
        user_id=shared_root.user_id,
        run_id=shared_root.run_id,
        tenant_id=shared_root.tenant_id,
        scope_id=shared_root.root_scope_id,
        scope_name=shared_root.root_scope_name,
        scope_kind="root",
        parent_scope_id=None,
        root_scope_id=shared_root.root_scope_id,
        cost_limit=shared_root.root_cost_limit,
        plan_key=shared_root.plan_key,
        reserve_id=shared_root.reserve_id,
        tariff=shared_root.tariff,
        tariff_version=shared_root.tariff_version,
        provider=shared_root.provider,
        provider_model_id=shared_root.provider_model_id,
        input_tokens=shared_root.input_tokens,
        cached_input_tokens=shared_root.cached_input_tokens,
        output_tokens=shared_root.output_tokens,
        tokens_used=shared_root.tokens_used,
        cost_used=shared_root.cost_used,
        shared_root=shared_root,
    )


def push_budget_scope(
    scope: BudgetState, *, inherit_active_source: bool = True
) -> Token:
    context = _ensure_execution_context()
    if context is None:
        source = _sole_active_budget_context() if inherit_active_source else None
        if source is not None:
            base_context = source.clone_for_current_path()
            new_context = BudgetExecutionContext(
                shared_root=base_context.shared_root,
                scope_stack=[*base_context.scope_stack, scope],
                owner_key=base_context.owner_key,
            )
        else:
            if scope.shared_root is None:
                raise ValueError(
                    "push_budget_scope requires a shared_root-backed scope."
                )
            new_context = BudgetExecutionContext(
                shared_root=scope.shared_root,
                scope_stack=[scope],
            )
    else:
        new_context = BudgetExecutionContext(
            shared_root=context.shared_root,
            scope_stack=[*context.scope_stack, scope],
            owner_key=context.owner_key,
        )
    return install_budget_context(new_context)


def pop_budget_scope(token: Token) -> None:
    reset_budget_context(token)


def require_budget_state() -> BudgetState:
    from actguard.exceptions import MissingRuntimeContextError

    state = get_budget_state()
    if state is None:
        raise MissingRuntimeContextError(
            "No active budget context. Wrap work with client.budget_guard(...)."
        )
    return state


def active_scope_metadata() -> Optional[dict]:
    state = get_budget_state()
    if state is None:
        return None
    return state.scope_metadata()


def blocked_scope_metadata(blocked_scope: BudgetState) -> dict:
    tokens_used, cost_used = blocked_scope.root_totals()
    if blocked_scope.scope_kind != "root" or blocked_scope.shared_root is None:
        tokens_used, cost_used = blocked_scope.tokens_used, blocked_scope.cost_used
    payload = blocked_scope.scope_metadata()
    payload["tokens_used"] = tokens_used
    payload["cost_used"] = cost_used
    payload["cost_limit"] = blocked_scope.cost_limit
    if blocked_scope.tariff_version:
        payload["tariff_version"] = blocked_scope.tariff_version
    elif blocked_scope.shared_root is not None and blocked_scope.shared_root.tariff_version:
        payload["tariff_version"] = blocked_scope.shared_root.tariff_version
    return payload


def record_usage(
    *,
    provider: str,
    provider_model_id: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> None:
    context = _ensure_execution_context()
    active_scope = context.active_scope() if context is not None else None
    if context is None or active_scope is None:
        source = _sole_active_budget_context()
        if source is not None and source.active_scope() is not None:
            context = source
            active_scope = source.active_scope()
        else:
            state = require_budget_state()
            if state.shared_root is not None:
                state.shared_root.record_usage(
                    provider=provider,
                    provider_model_id=provider_model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    scope_name=state.scope_name,
                )
            state.record_usage(
                provider=provider,
                provider_model_id=provider_model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
            )
            return

    if context is not None and active_scope is not None:
        context.shared_root.record_usage(
            provider=provider,
            provider_model_id=provider_model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            scope_name=active_scope.scope_name,
        )
        for scope in context.scope_stack:
            scope.record_usage(
                provider=provider,
                provider_model_id=provider_model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
            )
        return

    state = require_budget_state()
    if state.shared_root is not None:
        state.shared_root.record_usage(
            provider=provider,
            provider_model_id=provider_model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            scope_name=state.scope_name,
        )
    state.record_usage(
        provider=provider,
        provider_model_id=provider_model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def check_budget_limits() -> Optional[BudgetLimitViolation]:
    context = _ensure_execution_context()
    if context is None:
        context = _sole_active_budget_context()
        if context is None:
            state = get_budget_state()
            if state is None:
                return None
            if state.cost_limit is not None and state.cost_used >= state.cost_limit:
                return BudgetLimitViolation(active_scope=state, blocked_scope=state)
            if (
                state.shared_root is not None
                and state.shared_root.root_cost_limit is not None
                and state.shared_root.cost_used >= state.shared_root.root_cost_limit
            ):
                blocked_scope = build_root_scope_state(state.shared_root)
                blocked_scope.tokens_used = state.shared_root.tokens_used
                blocked_scope.cost_used = state.shared_root.cost_used
                return BudgetLimitViolation(
                    active_scope=state,
                    blocked_scope=blocked_scope,
                )
            return None

    active_scope = context.active_scope()
    if active_scope is None:
        return None

    for scope in reversed(context.scope_stack):
        if scope.cost_limit is not None and scope.cost_used >= scope.cost_limit:
            return BudgetLimitViolation(active_scope=active_scope, blocked_scope=scope)

    root_limit = context.shared_root.root_cost_limit
    if root_limit is not None and context.shared_root.cost_used >= root_limit:
        root_scope = build_root_scope_state(context.shared_root)
        root_scope.tokens_used = context.shared_root.tokens_used
        root_scope.cost_used = context.shared_root.cost_used
        return BudgetLimitViolation(active_scope=active_scope, blocked_scope=root_scope)

    return None
