import hashlib
import inspect
import json
from contextvars import ContextVar, Token
from typing import Any, Dict, Optional, Tuple


def extract_arg(fn, arg_name: str, args: tuple, kwargs: dict) -> Any:
    sig = inspect.signature(fn)
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    return bound.arguments[arg_name]


def validate_scope(fn, arg_name: str) -> None:
    """Raise ValueError at decoration time if arg_name is not a parameter of fn."""
    sig = inspect.signature(fn)
    if arg_name not in sig.parameters:
        raise ValueError(
            f"actguard: scope={arg_name!r} is not a parameter of {fn.__qualname__!r}. "
            f"Valid parameters: {list(sig.parameters)}"
        )


_ctx_session_id: ContextVar[Optional[str]] = ContextVar("_ctx_session_id", default=None)
_ctx_scope: ContextVar[Dict[str, str]] = ContextVar("_ctx_scope", default={})


def set_session(session_id: str, scope: Dict[str, str]) -> Tuple[Token, Token]:
    return _ctx_session_id.set(session_id), _ctx_scope.set(scope)


def reset_session(tokens: Tuple[Token, Token]) -> None:
    _ctx_session_id.reset(tokens[0])
    _ctx_scope.reset(tokens[1])


def get_session_id() -> Optional[str]:
    return _ctx_session_id.get()


def get_scope_hash() -> str:
    scope = _ctx_scope.get()
    if not scope:
        return "global"
    canonical = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
