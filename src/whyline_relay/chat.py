"""The `whyline-relay chat` REPL: setup, routing, and the turn pipeline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from whyline_relay import init

CHAT_AGENTS = ("claude", "codex", "agy", "grok")


class NoAgentsInstalled(RuntimeError):
    """None of claude/codex/agy/grok are on PATH."""


def chat_config_path(root: Path) -> Path:
    return root / ".whyline" / "relay" / "chat.json"


def load_default_agent(root: Path) -> str | None:
    path = chat_config_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    agent = data.get("default_agent")
    return agent if isinstance(agent, str) else None


def save_default_agent(root: Path, agent: str) -> None:
    path = chat_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"default_agent": agent}) + "\n")


def run_setup_wizard(
    root: Path,
    *,
    which=None,
    input_fn=None,
    print_fn=None,
) -> str:
    which = which if which is not None else shutil.which
    input_fn = input_fn if input_fn is not None else input
    print_fn = print_fn if print_fn is not None else print

    installed = [name for name in CHAT_AGENTS if which(name) is not None]
    if not installed:
        raise NoAgentsInstalled(
            "none of claude, codex, agy, grok were found on PATH"
        )
    missing = [name for name in CHAT_AGENTS if name not in installed]
    print_fn(f"Detected: {', '.join(installed)}.")
    if missing:
        print_fn(f"Not found: {', '.join(missing)}.")

    default_hint = installed[0]
    while True:
        answer = input_fn(f"Pick your default agent [{default_hint}]: ").strip()
        chosen = answer or default_hint
        if chosen in installed:
            break
        print_fn(f"{chosen} is not installed here -- pick one of: {', '.join(installed)}")

    save_default_agent(root, chosen)
    init.ensure_relay_gitignore(root)
    print_fn(f"Saved. Starting chat -- default agent is {chosen}.")
    return chosen
