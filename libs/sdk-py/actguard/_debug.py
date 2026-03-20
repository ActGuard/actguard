from __future__ import annotations

import logging
import os
import sys
from typing import Iterable, TextIO

_RESET = "\x1b[0m"
_BOLD_CYAN = "\x1b[1;36m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"
_CYAN = "\x1b[36m"

_TRANSPORT_KIND_COLORS = {
    "request": _GREEN,
    "request-body": _GREEN,
    "response": _CYAN,
    "response-body": _CYAN,
    "error": _RED,
}

_LOG_LEVEL_COLORS = {
    logging.DEBUG: _GREEN,
    logging.INFO: _CYAN,
    logging.WARNING: _YELLOW,
    logging.ERROR: _RED,
    logging.CRITICAL: _RED,
}

_HANDLER_MARKER = "_actguard_debug_handler"


def use_color(stream: TextIO | None = None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stderr
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except Exception:
        return False


def format_transport_debug(
    parts: Iterable[str],
    *,
    stream: TextIO | None = None,
) -> str:
    filtered = [part for part in parts if part]
    if not filtered:
        return ""
    if not use_color(stream):
        return " ".join(filtered)

    kind = filtered[0]
    prefix = _colorize("[actguard debug]", _BOLD_CYAN)
    kind_color = _TRANSPORT_KIND_COLORS.get(kind)
    if kind_color:
        filtered[0] = _colorize(kind, kind_color)
    return " ".join([prefix, *filtered])


def ensure_actguard_debug_handler() -> None:
    logger = logging.getLogger("actguard")
    for handler in logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            return

    handler = logging.StreamHandler()
    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_ActGuardColorFormatter())

    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


class _ActGuardColorFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("[actguard] %(name)s %(levelname)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        if not use_color(sys.stderr):
            return rendered

        prefix = _colorize("[actguard]", _BOLD_CYAN)
        level = _colorize(
            record.levelname,
            _LOG_LEVEL_COLORS.get(record.levelno, _CYAN),
        )
        rendered = rendered.replace("[actguard]", prefix, 1)
        return rendered.replace(record.levelname, level, 1)


def _colorize(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}"
