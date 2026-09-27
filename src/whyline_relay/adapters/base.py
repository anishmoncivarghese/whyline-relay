"""Shared adapter descriptions and diagnostics."""

from __future__ import annotations

import json
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
    extract_response: Callable[[str], str]
    uses_output_file: bool


def extract_or_fallback(text: str, field_names: tuple[str, ...]) -> str:
    """Try each field name in order against the last JSON line; else return
    the raw last non-blank line. Shared by claude and generic."""
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[-1].strip().startswith("{"):
        try:
            parsed = json.loads(lines[-1])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            for field in field_names:
                value = parsed.get(field)
                if isinstance(value, str):
                    return value
    if lines:
        return "".join(ch for ch in lines[-1].strip() if ch.isprintable())
    return ""


def last_line_detail(text: str) -> str:
    """'; its last output was: "<last non-blank line, printable characters only, at most 160>"' or ''."""
    lines = [line for line in text.splitlines() if line.strip()]
    if lines:
        last = "".join(ch for ch in lines[-1].strip() if ch.isprintable())[:160]
        return f'; its last output was: "{last}"'
    return ""
