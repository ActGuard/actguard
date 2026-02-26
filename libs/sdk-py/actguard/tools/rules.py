import re
from abc import ABC, abstractmethod
from typing import Any, Dict

from actguard.exceptions import GuardError
from actguard.tools import _facts


class Rule(ABC):
    @abstractmethod
    def check(
        self, arguments: Dict[str, Any], session_id: str, scope_hash: str
    ) -> None:
        """Raise GuardError if the rule is violated."""


class RequireFact(Rule):
    def __init__(self, arg: str, kind: str, hint: str = ""):
        self.arg = arg
        self.kind = kind
        self.hint = hint

    def check(
        self, arguments: Dict[str, Any], session_id: str, scope_hash: str
    ) -> None:
        val = arguments.get(self.arg)
        if val is None:
            return
        if isinstance(val, (list, tuple, set)):
            values = [str(v) for v in val]
        else:
            values = [str(val)]

        for v in values:
            if not _facts.exists(session_id, scope_hash, self.kind, v):
                default_hint = f"Call a read tool to fetch '{self.kind}' first."
                raise GuardError(
                    "MISSING_FACT",
                    f"Value {self.kind}={v!r} was not proven in this session.",
                    details={"kind": self.kind, "value": v},
                    fix_hint=self.hint or default_hint,
                )


class Threshold(Rule):
    def __init__(self, arg: str, max: float):
        self.arg = arg
        self.max = max

    def check(
        self, arguments: Dict[str, Any], session_id: str, scope_hash: str
    ) -> None:
        val = arguments.get(self.arg)
        if val is None:
            return
        if val > self.max:
            raise GuardError(
                "THRESHOLD_EXCEEDED",
                f"Argument {self.arg!r} value {val} exceeds maximum {self.max}.",
                details={"arg": self.arg, "value": val, "max": self.max},
                fix_hint=f"Use a value <= {self.max} for {self.arg!r}.",
            )


class BlockRegex(Rule):
    def __init__(self, arg: str, pattern: str):
        self.arg = arg
        self.pattern = pattern
        self._re = re.compile(pattern)

    def check(
        self, arguments: Dict[str, Any], session_id: str, scope_hash: str
    ) -> None:
        val = arguments.get(self.arg)
        if val is None:
            return
        if self._re.search(str(val)):
            raise GuardError(
                "PATTERN_BLOCKED",
                f"Argument {self.arg!r} value {val!r} matches blocked "
                f"pattern {self.pattern!r}.",
                details={"arg": self.arg, "value": val, "pattern": self.pattern},
                fix_hint=(
                    f"Provide a value for {self.arg!r} that does not "
                    f"match {self.pattern!r}."
                ),
            )
