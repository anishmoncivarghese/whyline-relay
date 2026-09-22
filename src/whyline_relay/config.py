"""Relay configuration: built-in defaults, overridden by .whyline/relay/config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from whyline_relay import adapters

DEFAULTS = {
    "plan": "plan.md",
    "max_rounds": 3,
    "timeout_minutes": 30,
    "branch_prefix": "relay/",
    "agents": {
        name: list(adapter.default_command)
        for name, adapter in adapters.BUILTIN.items()
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
class Roles:
    implementer: str = "codex"
    reviewer: str = "claude"


@dataclass(frozen=True)
class Config:
    plan: str
    max_rounds: int
    timeout_minutes: int
    branch_prefix: str
    agents: dict[str, list[str]]
    status_map: dict[str, str]
    roles: Roles = field(default_factory=Roles)
    adapters: dict[str, str] = field(default_factory=dict)
    backups: dict[str, str] = field(default_factory=dict)


def adapter_for(settings: Config, agent: str) -> adapters.Adapter:
    return adapters.get(settings.adapters.get(agent, agent))


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
    configured_adapters: dict[str, str] = {}
    for name, table in (raw.get("agents") or {}).items():
        if name in adapters.BUILTIN:
            if "adapter" in table:
                raise ConfigError(f"agent '{name}' is built in and cannot set adapter")
            resolved_adapter = adapters.get(name)
        else:
            if "adapter" not in table:
                raise ConfigError(
                    f"agent '{name}' is not built in: set adapter = \"generic\" "
                    f"and a command under [agents.{name}]"
                )
            adapter_name = table["adapter"]
            if adapter_name == "generic":
                resolved_adapter = adapters.GENERIC
                configured_adapters[name] = "generic"
            elif adapter_name in adapters.BUILTIN:
                resolved_adapter = adapters.get(adapter_name)
                configured_adapters[name] = adapter_name
            else:
                builtins = ", ".join(sorted(adapters.BUILTIN))
                raise ConfigError(
                    f'[agents.{name}] adapter must be "generic" or a built-in agent '
                    f'({builtins}), not {adapter_name!r}'
                )
            if resolved_adapter.default_command is None:
                command = table.get("command")
                if not isinstance(command, list) or not command:
                    raise ConfigError(f"[agents.{name}] needs a non-empty command")
        command = table.get("command")
        if command is not None:
            if not isinstance(command, list) or not command:
                raise ConfigError(f"[agents.{name}] needs a non-empty command")
            agents[name] = list(command)
        elif name not in agents:
            agents[name] = list(resolved_adapter.default_command)
        model = table.get("model")
        if model is not None:
            if not isinstance(model, str) or not model:
                raise ConfigError(f"[agents.{name}] model must be a non-empty string")
            if resolved_adapter.model_flag is None:
                raise ConfigError(
                    f"[agents.{name}] cannot set model: the {resolved_adapter.name} "
                    "adapter has no way to apply it"
                )
            agents[name] = [*agents[name], *resolved_adapter.model_flag, model]

    role_values = raw.get("roles") or {}
    for key in role_values:
        if key not in ("implementer", "reviewer", "backup"):
            raise ConfigError(
                f"[roles] has an unknown key '{key}' (use implementer or reviewer)"
            )
    role_names = {
        "implementer": role_values.get("implementer", Roles.implementer),
        "reviewer": role_values.get("reviewer", Roles.reviewer),
    }
    for role, name in role_names.items():
        if not isinstance(name, str):
            raise ConfigError(f"[roles] {role} must be a string")
        if name not in adapters.BUILTIN and name not in configured_adapters:
            builtins = ", ".join(sorted(adapters.BUILTIN))
            raise ConfigError(
                f"role '{role}' names '{name}', which is not a built-in agent "
                f"({builtins}) or a configured generic agent"
            )

    backup_values = role_values.get("backup") or {}
    for key in backup_values:
        if key not in ("implementer", "reviewer"):
            raise ConfigError(
                f"[roles.backup] has an unknown key '{key}' "
                f"(use implementer or reviewer)"
            )
        value = backup_values[key]
        if not isinstance(value, str):
            raise ConfigError(f"[roles.backup] {key} must be a string")
        if value == role_names[key]:
            raise ConfigError(
                f"[roles.backup] {key} cannot be the same as its own agent"
            )
        if value not in adapters.BUILTIN and value not in configured_adapters:
            builtins = ", ".join(sorted(adapters.BUILTIN))
            raise ConfigError(
                f"[roles.backup] {key} names '{value}', which is not a built-in "
                f"agent ({builtins}) or a configured generic agent"
            )

    status_map = {**DEFAULTS["status_map"], **(raw.get("status_map") or {})}
    return Config(
        plan=raw.get("plan", DEFAULTS["plan"]),
        max_rounds=int(raw.get("max_rounds", DEFAULTS["max_rounds"])),
        timeout_minutes=int(raw.get("timeout_minutes", DEFAULTS["timeout_minutes"])),
        branch_prefix=raw.get("branch_prefix", DEFAULTS["branch_prefix"]),
        agents=agents,
        status_map=status_map,
        roles=Roles(**role_names),
        adapters=configured_adapters,
        backups=dict(backup_values),
    )
