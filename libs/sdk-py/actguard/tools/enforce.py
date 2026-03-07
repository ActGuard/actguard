import functools
import inspect
from typing import List

from actguard.exceptions import GuardError
from actguard.tools._observability import emit_guard_blocked
from actguard.tools._scope import get_scope_hash, get_session_id
from actguard.tools.rules import Rule


def enforce(rules: List[Rule]):
    """Decorator that checks chain-of-custody rules before a tool executes.

    Args:
        rules: List of Rule instances (RequireFact, Threshold, BlockRegex, etc.)
               checked in order; first failure raises GuardError.
    """

    def decorator(fn):
        sig = inspect.signature(fn)
        tool_name = fn.__qualname__

        def _check(args: tuple, kwargs: dict, session_id: str) -> None:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            scope_hash = get_scope_hash()
            for rule in rules:
                rule.check(bound.arguments, session_id, scope_hash)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                session_id = get_session_id()
                if session_id is None:
                    raise GuardError(
                        "NO_SESSION",
                        "No active ActGuard session.",
                        fix_hint="Wrap your agent loop with actguard.session().",
                    )
                _check(args, kwargs, session_id)
                return fn(*args, **kwargs)
            except GuardError as exc:
                emit_guard_blocked(tool_name, "enforce", exc)
                raise

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                session_id = get_session_id()
                if session_id is None:
                    raise GuardError(
                        "NO_SESSION",
                        "No active ActGuard session.",
                        fix_hint="Wrap your agent loop with actguard.session().",
                    )
                _check(args, kwargs, session_id)
                return await fn(*args, **kwargs)
            except GuardError as exc:
                emit_guard_blocked(tool_name, "enforce", exc)
                raise

        return async_wrapper if inspect.iscoroutinefunction(fn) else wrapper

    return decorator
