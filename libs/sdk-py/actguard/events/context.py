from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional


_tool_name: ContextVar[Optional[str]] = ContextVar("_tool_name", default=None)


def get_tool_name() -> Optional[str]:
    return _tool_name.get()


def set_tool_name(tool_name: str) -> Token:
    return _tool_name.set(tool_name)


def reset_tool_name(token: Token) -> None:
    _tool_name.reset(token)
