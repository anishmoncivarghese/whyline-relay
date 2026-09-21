"""Declaring permission up front, so no agent run needs an interactive prompt."""

from __future__ import annotations

import json
from pathlib import Path

from whyline_relay import config, prompts

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


def detect_stack(root: Path) -> str:
    if (root / "pyproject.toml").exists():
        return "python"
    if (root / "package.json").exists():
        return "node"
    return "base"


def allowlist(stack: str) -> dict:
    return {
        "permissions": {
            "allow": [*BASE_ALLOW, *PRESETS.get(stack, [])],
            "deny": list(DENY),
        }
    }


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run(root: Path, *, assume_yes: bool, confirm=input) -> int:
    stack = detect_stack(root)
    proposed = allowlist(stack)
    settings_path = config.relay_dir(root) / "claude-settings.json"

    print(f"Detected stack: {stack}")
    print(f"Proposed {settings_path}:")
    print(json.dumps(proposed, indent=2))
    print("Also writing prompt templates and config under .whyline/relay/.")
    if not assume_yes:
        try:
            answer = confirm("Write these? [Y/n] ").strip().lower()
        except EOFError:  # no terminal: writing files nobody agreed to is worse
            answer = "n"
        if answer and not answer.startswith("y"):
            print("Nothing written.")
            return 1

    relay = config.relay_dir(root)
    _write(settings_path, json.dumps(proposed, indent=2) + "\n")
    _write(relay / "prompts" / "implement.md", prompts.IMPLEMENT)
    _write(relay / "prompts" / "review.md", prompts.REVIEW)
    codex = " ".join(config.DEFAULTS["agents"]["codex"])
    claude = " ".join(config.DEFAULTS["agents"]["claude"])
    _write(
        relay / "config.toml",
        "# whyline-relay configuration. Every key is optional.\n"
        f'plan = "{config.DEFAULTS["plan"]}"\n'
        f"max_rounds = {config.DEFAULTS['max_rounds']}\n"
        f"timeout_minutes = {config.DEFAULTS['timeout_minutes']}\n"
        f'branch_prefix = "{config.DEFAULTS["branch_prefix"]}"\n\n'
        "[agents.codex]\n"
        f"command = {json.dumps(codex.split())}\n\n"
        "[agents.claude]\n"
        f"command = {json.dumps(claude.split())}\n",
    )
    _write(relay / ".gitignore", "logs/\nstate.json\nSTOP\nrunning.json\n")

    print(f"Wrote {settings_path} and {relay}.")
    print(
        "\nClaude reads these permissions through `--settings`, not through "
        ".claude/settings.json, which Claude ignores in a workspace nobody has "
        "trusted interactively. The relay puts `whyline sync` output into every "
        "prompt itself, so Codex hooks are not needed for relay runs. Commit these "
        "files before `whyline-relay start`, which refuses a dirty working tree."
    )
    print(
        "\nThe allowlist is a convenience, not a sandbox: `uv run`, `npm` and `npx` "
        "execute arbitrary project code. Run unattended relays on an isolated "
        "branch or repository that holds no secrets."
    )
    return 0
