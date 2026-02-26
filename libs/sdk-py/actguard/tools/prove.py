import functools
import inspect
from typing import Callable, Union

from actguard.exceptions import GuardError
from actguard.tools._facts import mint
from actguard.tools._scope import get_scope_hash, get_session_id


def prove(
    kind: str,
    extract: Union[str, Callable],
    ttl: float = 300,
    max_items: int = 200,
    on_too_many: str = "block",
):
    """Decorator that mints verified facts from a tool's return value.

    Args:
        kind: The fact kind/category (e.g. "order_id", "user_id").
        extract: String key/attribute name, or callable receiving the full
                 result and returning a value or list of values.
        ttl: Seconds before minted facts expire. Defaults to 300.
        max_items: Maximum number of facts to mint. Defaults to 200.
        on_too_many: "block" raises GuardError, "truncate" mints only max_items.
    """

    def decorator(fn):
        def _extract_and_mint(result, session_id: str) -> None:
            # Normalize to list for extraction
            if result is None:
                items = []
            elif isinstance(result, list):
                items = result
            else:
                items = [result]

            # Extract values
            if callable(extract):
                raw = extract(result)
                if raw is None:
                    values = []
                elif isinstance(raw, list):
                    values = [str(v) for v in raw]
                else:
                    values = [str(raw)]
            else:
                values = []
                for item in items:
                    try:
                        val = item[extract]
                    except (KeyError, TypeError):
                        try:
                            val = getattr(item, extract)
                        except AttributeError:
                            continue
                    values.append(str(val))

            # Quantity check
            if len(values) > max_items:
                if on_too_many == "block":
                    raise GuardError(
                        "TOO_MANY_RESULTS",
                        f"Tool returned {len(values)} items for kind={kind!r}, "
                        f"exceeding max_items={max_items}.",
                        details={
                            "kind": kind,
                            "count": len(values),
                            "max_items": max_items,
                        },
                        fix_hint=(
                            f"Narrow your query to return at most {max_items} results."
                        ),
                    )
                values = values[:max_items]

            scope_hash = get_scope_hash()
            for v in values:
                mint(session_id, scope_hash, kind, v, ttl)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            session_id = get_session_id()
            if session_id is None:
                raise GuardError(
                    "NO_SESSION",
                    "No active ActGuard session.",
                    fix_hint="Wrap your agent loop with actguard.session().",
                )
            result = fn(*args, **kwargs)
            _extract_and_mint(result, session_id)
            return result

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            session_id = get_session_id()
            if session_id is None:
                raise GuardError(
                    "NO_SESSION",
                    "No active ActGuard session.",
                    fix_hint="Wrap your agent loop with actguard.session().",
                )
            result = await fn(*args, **kwargs)
            _extract_and_mint(result, session_id)
            return result

        return async_wrapper if inspect.iscoroutinefunction(fn) else wrapper

    return decorator
