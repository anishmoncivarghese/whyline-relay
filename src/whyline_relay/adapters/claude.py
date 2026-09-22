"""Claude adapter, including its relay-managed permission file."""

from __future__ import annotations

import json

from whyline_relay.adapters.base import Adapter, Manages, last_line_detail

BASE_ALLOW = [
    "Edit",
    "Bash(git add:*)",
    "Bash(git commit:*)",
    "Bash(git diff:*)",
    "Bash(git status:*)",
    "Bash(git log:*)",
    "Bash(tail:*)",
    "Bash(head:*)",
    "Bash(wc:*)",
    "Bash(grep:*)",
    "Bash(ls:*)",
    "Bash(whyline:*)",
]

DENY = ["Bash(git push:*)", "Bash(rm -rf:*)"]

PRESETS = {
    "python": [
        "Bash(pytest:*)",
        "Bash(uv run pytest:*)",
        "Bash(uv run:*)",
        "Bash(.venv/bin/pytest:*)",
        "Bash(.venv/bin/python:*)",
    ],
    "node": ["Bash(npm test:*)", "Bash(npm run:*)", "Bash(npx:*)"],
    "base": [],
}


def allowlist(stack: str) -> dict:
    return {
        "permissions": {
            "allow": [*BASE_ALLOW, *PRESETS.get(stack, [])],
            "deny": list(DENY),
        }
    }


def permission_files(stack: str) -> dict[str, str]:
    return {"claude-settings.json": json.dumps(allowlist(stack), indent=2) + "\n"}


def diagnose(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[-1].startswith("{"):
        try:
            result = json.loads(lines[-1])
        except ValueError:
            result = None
        denials = result.get("permission_denials", []) if isinstance(result, dict) else []
        commands = [
            str(d.get("tool_input", {}).get("command", d.get("tool_name", "?")))[:60]
            for d in denials
            if isinstance(d, dict)
        ]
        if commands:
            return (
                f"; it was denied permission to run: {', '.join(commands)}. "
                "Check .whyline/relay/claude-settings.json"
            )
    return last_line_detail(text)


ADAPTER = Adapter(
    name="claude",
    default_command=(
        "claude",
        "-p",
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "json",
        "--settings",
        ".whyline/relay/claude-settings.json",
    ),
    binary="claude",
    login_argv=("claude", "auth", "status"),
    login_fix="claude auth login",
    permission_files=permission_files,
    diagnose=diagnose,
    manages=Manages(True, True, True),
)
