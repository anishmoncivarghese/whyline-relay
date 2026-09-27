"""Codex adapter."""

from __future__ import annotations

from whyline_relay.adapters.base import Adapter, Manages, last_line_detail


def extract_response(text: str) -> str:
    return text.strip()


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
    model_flag=("--model",),
    extract_response=extract_response,
    uses_output_file=True,
)
