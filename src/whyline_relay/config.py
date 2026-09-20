"""Relay configuration: built-in defaults, overridden by .whyline/relay/config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULTS = {
    "plan": "plan.md",
    "max_rounds": 3,
    "timeout_minutes": 30,
    "branch_prefix": "relay/",
    "agents": {
        "codex": ["codex", "exec", "-s", "workspace-write", "--color", "never"],
        "claude": [
            "claude",
            "-p",
            "--permission-mode",
            "acceptEdits",
            "--output-format",
            "json",
        ],
    },
    "status_map": {
        "review": "ready-for-review",
        "changes": "changes-requested",
        "approved": "approved",
        "blocked": "blocked",
        "assigned": "assigned",
    },
}


class ConfigError(ValueError):
    """The config file exists but cannot be used."""


@dataclass(frozen=True)
class Config:
    plan: str
    max_rounds: int
    timeout_minutes: int
    branch_prefix: str
    agents: dict[str, list[str]]
    status_map: dict[str, str]


def relay_dir(root: Path) -> Path:
    return root / ".whyline" / "relay"


def config_path(root: Path) -> Path:
    return relay_dir(root) / "config.toml"


def load(root: Path) -> Config:
    """Read config.toml if present, layering it over DEFAULTS."""
    path = config_path(root)
    raw: dict = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as error:
            raise ConfigError(f"could not read {path.name} ({path}): {error}") from error
    agents = dict(DEFAULTS["agents"])
    for name, table in (raw.get("agents") or {}).items():
        command = table.get("command")
        if command is not None:
            agents[name] = list(command)
    status_map = {**DEFAULTS["status_map"], **(raw.get("status_map") or {})}
    return Config(
        plan=raw.get("plan", DEFAULTS["plan"]),
        max_rounds=int(raw.get("max_rounds", DEFAULTS["max_rounds"])),
        timeout_minutes=int(raw.get("timeout_minutes", DEFAULTS["timeout_minutes"])),
        branch_prefix=raw.get("branch_prefix", DEFAULTS["branch_prefix"]),
        agents=agents,
        status_map=status_map,
    )
