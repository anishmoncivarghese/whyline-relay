"""Codex adapter."""

from __future__ import annotations

from whyline_relay.adapters.base import Adapter, Manages, last_line_detail

ADAPTER = Adapter(
    name="codex",
    default_command=(
        "codex",
        "exec",
        "-s",
        "workspace-write",
        "--color",
        "never",
    ),
    binary="codex",
    login_argv=("codex", "login", "status"),
    login_fix="codex login",
    permission_files=lambda stack: {},
    diagnose=last_line_detail,
    manages=Manages(True, True, True),
)
