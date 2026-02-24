import inspect
from typing import Any


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
