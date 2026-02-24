from __future__ import annotations

from enum import Enum


class Mode(str, Enum):
    HAPPY = "happy"
    SLOW_DEPENDENCY = "slow_dependency"
    DEPENDENCY_DOWN = "dependency_down"
    LOOP = "loop"
    RETRY_DUPLICATE = "retry_duplicate"


MODE_NOTIFY_ATTEMPTS = {
    Mode.HAPPY: 1,
    Mode.SLOW_DEPENDENCY: 1,
    Mode.DEPENDENCY_DOWN: 1,
    Mode.LOOP: 3,
    Mode.RETRY_DUPLICATE: 1,
}


def parse_mode(raw: str) -> Mode:
    try:
        return Mode(raw)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in Mode)
        raise ValueError(f"Invalid mode '{raw}'. Allowed values: {allowed}") from exc


def notify_attempts(mode: Mode) -> int:
    return MODE_NOTIFY_ATTEMPTS[mode]


def should_duplicate_incident(mode: Mode) -> bool:
    return mode is Mode.RETRY_DUPLICATE
