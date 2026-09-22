"""Declaring permission up front, so no agent run needs an interactive prompt."""

from __future__ import annotations

import json
from pathlib import Path

from whyline_relay import adapters, config, invocation, prompts
from whyline_relay.adapters.claude import BASE_ALLOW, DENY, PRESETS, allowlist


def detect_stack(root: Path) -> str:
    if (root / "pyproject.toml").exists():
        return "python"
    if (root / "package.json").exists():
        return "node"
    return "base"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run(
    root: Path,
    *,
    assume_yes: bool,
    overwrite: bool = False,
    confirm=input,
    implementer: str | None = None,
    reviewer: str | None = None,
) -> int:
    roles_given = implementer is not None or reviewer is not None
    implementer = implementer or "codex"
    reviewer = reviewer or "claude"
    agents_in_use = list(dict.fromkeys((implementer, reviewer)))
    stack = detect_stack(root)
    permission_files: dict[str, str] = {}
    for name in agents_in_use:
        permission_files.update(adapters.BUILTIN[name].permission_files(stack))
    relay = config.relay_dir(root)

    print(f"Detected stack: {stack}")
    for key, text in permission_files.items():
        print(f"Proposed {relay / key}:")
        print(text, end="" if text.endswith("\n") else "\n")
    print("Also writing prompt templates and config under .whyline/relay/.")
    if not assume_yes:
        try:
            answer = confirm("Write these? [Y/n] ").strip().lower()
        except EOFError:  # no terminal: writing files nobody agreed to is worse
            answer = "n"
        if answer and not answer.startswith("y"):
            print("Nothing written.")
            return 1

    header = (
        "# whyline-relay configuration. Every key is optional.\n"
        f'plan = "{config.DEFAULTS["plan"]}"\n'
        f"max_rounds = {config.DEFAULTS['max_rounds']}\n"
        f"timeout_minutes = {config.DEFAULTS['timeout_minutes']}\n"
        f'branch_prefix = "{config.DEFAULTS["branch_prefix"]}"\n\n'
    )
    if roles_given:
        config_text = (
            header
            + "[roles]\n"
            + f'implementer = "{implementer}"\n'
            + f'reviewer = "{reviewer}"\n\n'
        )
        for name in agents_in_use:
            command = config.DEFAULTS["agents"][name]
            config_text += (
                f"[agents.{name}]\n"
                f"command = {json.dumps(command)}\n"
            )
            if name != agents_in_use[-1]:
                config_text += "\n"
    else:
        codex = " ".join(config.DEFAULTS["agents"]["codex"])
        claude = " ".join(config.DEFAULTS["agents"]["claude"])
        config_text = (
            header
            + "[agents.codex]\n"
            + f"command = {json.dumps(codex.split())}\n\n"
            + "[agents.claude]\n"
            + f"command = {json.dumps(claude.split())}\n"
        )
    generated = [
        *((relay / key, text) for key, text in permission_files.items()),
        (relay / "prompts" / "implement.md", prompts.IMPLEMENT),
        (relay / "prompts" / "review.md", prompts.REVIEW),
        (relay / "config.toml", config_text),
        (relay / ".gitignore", "logs/\nstate.json\nSTOP\nrunning.json\n"),
    ]
    written: list[Path] = []
    kept: list[Path] = []
    for path, content in generated:
        if overwrite or not path.exists():
            _write(path, content)
            written.append(path)
        elif path.read_bytes() != content.encode("utf-8"):
            kept.append(path)

    for path in written:
        print(f"Wrote {path.relative_to(root)}.")
    for path in kept:
        relative = path.relative_to(root)
        print(
            f"Kept {relative}: it already exists and differs. "
            "Run with --overwrite to replace it."
        )
    if "claude" in agents_in_use:
        print(
            "\nClaude reads these permissions through `--settings`, not through "
            ".claude/settings.json, which Claude ignores in a workspace nobody has "
            "trusted interactively. The relay puts `whyline sync` output into every "
            "prompt itself, so Codex hooks are not needed for relay runs. Commit these "
            f"files before `{invocation.command('start')}`, which refuses a dirty "
            "working tree."
        )
        print(
            "\nThe allowlist is a convenience, not a sandbox: `uv run`, `npm` and `npx` "
            "execute arbitrary project code. Run unattended relays on an isolated "
            "branch or repository that holds no secrets."
        )
    return 0
