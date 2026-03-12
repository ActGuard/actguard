from typing import Dict, Optional, Tuple

from actguard.exceptions import SessionUsageError
from actguard.tools._scope import reset_session, set_session


class GuardSession:
    """Context manager that activates a Chain-of-Custody session."""

    def __init__(self, id: str, scope: Dict[str, str] = None) -> None:
        self.id = id
        self.scope = scope or {}
        for key, value in self.scope.items():
            if not isinstance(value, str):
                raise SessionUsageError(
                    f"Scope values must be strings, got {type(value)} for key {key!r}"
                )
        self._tokens: Optional[Tuple] = None

    def __enter__(self) -> "GuardSession":
        self._tokens = set_session(self.id, self.scope)
        return self

    def __exit__(self, *_) -> None:
        reset_session(self._tokens)
        self._tokens = None

    async def __aenter__(self) -> "GuardSession":
        return self.__enter__()

    async def __aexit__(self, *args) -> None:
        return self.__exit__(*args)


def session(id: str, scope: Dict[str, str] = None) -> GuardSession:
    """Factory for a GuardSession context manager."""
    return GuardSession(id=id, scope=scope)
