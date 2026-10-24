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
    dir_flag: tuple[str, ...] | None
    extract_response: Callable[[str], str]
    uses_output_file: bool


def json_object(text: str) -> dict | None:
    """Return a JSON object from a complete document or trailing JSON block.

    Headless tools are inconsistent here: Claude emits one compact JSON line,
    while Grok's ``--output-format json`` currently pretty-prints one object
    and may prefix it with a permission diagnostic.
    """
    stripped = text.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    lines = [line for line in text.splitlines() if line.strip()]
    for index in range(len(lines) - 1, -1, -1):
        if not lines[index].lstrip().startswith("{"):
            continue
        try:
            parsed = json.loads("\n".join(lines[index:]))
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def extract_or_fallback(text: str, field_names: tuple[str, ...]) -> str:
    """Try fields against a complete object or last JSON line, then raw text.

    Shared by claude and generic.
    """
    parsed = json_object(text)
    if parsed is not None:
        for field in field_names:
            value = parsed.get(field)
            if isinstance(value, str):
                return value
    lines = [line for line in text.splitlines() if line.strip()]
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
