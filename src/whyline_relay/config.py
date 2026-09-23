"""Relay configuration: built-in defaults, overridden by .whyline/relay/config.toml."""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from whyline_relay import adapters, pipeline as pipeline_module

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


def _pipeline_fingerprint(raw_pipeline: dict) -> str:
    """A short hash of the whole [pipeline] table, for crash-safe resume.

    A paused task's saved stage/visit counts only mean what they meant when it
    paused. If [pipeline] changes before `resume`, this changes too, so the
    relay can refuse to guess rather than replay stale stage state against a
    reshaped graph.
    """
    canonical = json.dumps(raw_pipeline, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _load_pipeline(
    raw_pipeline: dict, role_names: dict[str, str]
) -> "pipeline_module.Pipeline":
    """Compile [pipeline]/[pipeline.profiles]/[pipeline.stages.*] into a Pipeline.

    "@next" is intentionally left unresolved here: the same stage can mean a
    different next stage in different profiles, so pipeline.decide() resolves
    it live, against whichever profile is actually running -- see pipeline.py.
    """
    stages_raw = raw_pipeline.get("stages") or {}
    if not stages_raw:
        raise ConfigError(
            "[pipeline] needs at least one stage under [pipeline.stages.<id>]"
        )
    stages: dict[str, pipeline_module.Stage] = {}
    for stage_id, table in stages_raw.items():
        role = table.get("role")
        if not isinstance(role, str) or not role:
            raise ConfigError(f"[pipeline.stages.{stage_id}] needs a role")
        if role not in role_names:
            raise ConfigError(
                f"[pipeline.stages.{stage_id}] names role {role!r}, "
                "which is not in [roles]"
            )
        prompt_name = table.get("prompt")
        if not isinstance(prompt_name, str) or not prompt_name:
            raise ConfigError(f"[pipeline.stages.{stage_id}] needs a prompt")
        on = table.get("on") or {}
        if not isinstance(on, dict) or not on:
            raise ConfigError(
                f"[pipeline.stages.{stage_id}] needs at least one outcome under "
                f"[pipeline.stages.{stage_id}.on]"
            )
        transitions: dict[str, str] = {}
        for outcome, target in on.items():
            if not isinstance(target, str) or not target:
                raise ConfigError(
                    f"[pipeline.stages.{stage_id}.on] {outcome!r} must be a "
                    "non-empty string"
                )
            transitions[outcome] = target
        max_visits = table.get("max_visits", 3)
        if (
            not isinstance(max_visits, int)
            or isinstance(max_visits, bool)
            or max_visits < 1
        ):
            raise ConfigError(
                f"[pipeline.stages.{stage_id}] max_visits must be a positive integer"
            )
        stages[stage_id] = pipeline_module.Stage(
            id=stage_id,
            role=role,
            prompt=prompt_name,
            transitions=transitions,
            max_visits=max_visits,
        )

    profiles_raw = raw_pipeline.get("profiles") or {}
    if not profiles_raw:
        raise ConfigError(
            "[pipeline] needs at least one profile under [pipeline.profiles]"
        )
    profiles: dict[str, pipeline_module.Profile] = {}
    for name, stage_list in profiles_raw.items():
        if not isinstance(stage_list, list) or not stage_list:
            raise ConfigError(
                f"[pipeline.profiles] {name!r} needs a non-empty list of stage ids"
            )
        for sid in stage_list:
            if sid not in stages:
                raise ConfigError(
                    f"[pipeline.profiles] {name!r} names unknown stage {sid!r}"
                )
        profiles[name] = pipeline_module.Profile(name=name, stages=tuple(stage_list))

    default_profile = raw_pipeline.get("default_profile")
    if not isinstance(default_profile, str) or default_profile not in profiles:
        raise ConfigError(
            "[pipeline] default_profile must name a profile under "
            "[pipeline.profiles]"
        )

    for stage in stages.values():
        for outcome, target in stage.transitions.items():
            if target in ("@complete", "@blocked"):
                continue
            if target == "@next":
                containing = [p for p in profiles.values() if stage.id in p.stages]
                if not containing:
                    raise ConfigError(
                        f'[pipeline.stages.{stage.id}.on] {outcome!r} uses "@next" '
                        f"but stage {stage.id!r} is not in any profile"
                    )
                for prof in containing:
                    index = prof.stages.index(stage.id)
                    if index + 1 >= len(prof.stages):
                        raise ConfigError(
                            f'[pipeline.stages.{stage.id}.on] {outcome!r} uses '
                            f'"@next" on the last stage of profile {prof.name!r}'
                        )
                continue

    for prof in profiles.values():
        reaches_complete = False
        for index, sid in enumerate(prof.stages):
            for target in stages[sid].transitions.values():
                resolved = target
                if target == "@next":
                    resolved = (
                        prof.stages[index + 1]
                        if index + 1 < len(prof.stages)
                        else None
                    )
                if resolved == "@complete":
                    reaches_complete = True
        if not reaches_complete:
            raise ConfigError(
                f'[pipeline.profiles] {prof.name!r} has no stage that can reach '
                '"@complete"'
            )

    for stage in stages.values():
        for outcome, target in stage.transitions.items():
            if (
                target not in ("@complete", "@blocked", "@next")
                and target not in stages
            ):
                raise ConfigError(
                    f"[pipeline.stages.{stage.id}.on] {outcome!r} names unknown "
                    f"stage {target!r}"
                )

    roles = {
        name: pipeline_module.Role(name=name, agent=agent)
        for name, agent in role_names.items()
    }
    return pipeline_module.Pipeline(
        roles=roles,
        stages=stages,
        profiles=profiles,
        default_profile=default_profile,
        legacy=False,
    )


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
    pipeline: "pipeline_module.Pipeline | None" = None
    pipeline_fingerprint: str = ""


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
    raw_pipeline = raw.get("pipeline")
    if raw_pipeline is not None:
        if "backup" in role_values:
            raise ConfigError(
                "[roles.backup] is not supported together with [pipeline]"
            )
        if "status_map" in raw:
            raise ConfigError(
                "[status_map] is not supported together with [pipeline]; a "
                "pipeline's stages use their own outcome labels"
            )
        role_names: dict[str, str] = {}
        for key, value in role_values.items():
            if not isinstance(value, str):
                raise ConfigError(f"[roles] {key} must be a string")
            role_names[key] = value
        for role, name in role_names.items():
            if name not in adapters.BUILTIN and name not in configured_adapters:
                builtins = ", ".join(sorted(adapters.BUILTIN))
                raise ConfigError(
                    f"role '{role}' names '{name}', which is not a built-in agent "
                    f"({builtins}) or a configured generic agent"
                )
        compiled_pipeline = _load_pipeline(raw_pipeline, role_names)
        pipeline_fp = _pipeline_fingerprint(raw_pipeline)
        backup_values: dict[str, str] = {}
        status_map = dict(DEFAULTS["status_map"])
        roles_obj = Roles()
    else:
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

        compiled_pipeline = None
        pipeline_fp = ""
        status_map = {**DEFAULTS["status_map"], **(raw.get("status_map") or {})}
        roles_obj = Roles(**role_names)

    return Config(
        plan=raw.get("plan", DEFAULTS["plan"]),
        max_rounds=int(raw.get("max_rounds", DEFAULTS["max_rounds"])),
        timeout_minutes=int(raw.get("timeout_minutes", DEFAULTS["timeout_minutes"])),
        branch_prefix=raw.get("branch_prefix", DEFAULTS["branch_prefix"]),
        agents=agents,
        status_map=status_map,
        roles=roles_obj,
        adapters=configured_adapters,
        backups=dict(backup_values),
        pipeline=compiled_pipeline,
        pipeline_fingerprint=pipeline_fp,
    )
