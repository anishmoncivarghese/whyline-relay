"""Shared adapter descriptions and diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Manages:
    permissions: bool
    login: bool
    denials: bool


@dataclass(frozen=True)
class Adapter:
    name: str
    default_command: tuple[str, ...] | None
    binary: str | None
    login_argv: tuple[str, ...] | None
    login_fix: str | None
    permission_files: Callable[[str], dict[str, str]]
    diagnose: Callable[[str], str]
    manages: Manages
    model_flag: tuple[str, ...] | None


def last_line_detail(text: str) -> str:
    """'; its last output was: "<last non-blank line, printable characters only, at most 160>"' or ''."""
    lines = [line for line in text.splitlines() if line.strip()]
    if lines:
        last = "".join(ch for ch in lines[-1].strip() if ch.isprintable())[:160]
        return f'; its last output was: "{last}"'
    return ""
