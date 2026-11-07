"""Inspecting a role's agent, clearing a sticky switch, and setting it."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from whyline_relay import adapters, config, failover, state, whyline_model


class RoleSetError(ValueError):
    """A `roles set` request cannot be honored as given."""


@dataclass(frozen=True)
class ModelOverride:
    model: str
    since: str  # ISO timestamp
    # Outlives the plan completing; config.toml carries it for later runs.
    keep: bool = False


class UnreadableModelOverrides(ValueError):
    """model-overrides.json exists but is not a usable record."""


def model_overrides_path(root: Path) -> Path:
    return config.relay_dir(root) / "model-overrides.json"


def read_model_overrides(root: Path) -> dict[str, ModelOverride]:
    """Every agent's model switch. A missing file is no switches; one that
    cannot be read raises UnreadableModelOverrides, so the loop can say so
    rather than silently running a model nobody asked for."""
    target = model_overrides_path(root)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise UnreadableModelOverrides(str(error)) from error
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise UnreadableModelOverrides(str(error)) from error
    if not isinstance(raw, dict):
        raise UnreadableModelOverrides("not a JSON object")
    result: dict[str, ModelOverride] = {}
    for agent, record in raw.items():
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("model"), str)
            or not record["model"]
            or not isinstance(record.get("since"), str)
        ):
            raise UnreadableModelOverrides(f"the entry for {agent} is not usable")
        keep = record.get("keep", False)
        result[agent] = ModelOverride(
            model=record["model"],
            since=record["since"],
            keep=keep if isinstance(keep, bool) else False,
        )
    return result


def _write_model_overrides(root: Path, overrides: dict[str, ModelOverride]) -> None:
    target = model_overrides_path(root)
    if not overrides:
        target.unlink(missing_ok=True)
        return
    state._write_atomic(
        target,
        json.dumps({a: asdict(o) for a, o in overrides.items()}, indent=2) + "\n",
    )


def _readable_model_overrides(root: Path) -> dict[str, ModelOverride]:
    try:
        return read_model_overrides(root)
    except UnreadableModelOverrides:
        return {}


def clear_model_overrides(
    root: Path, settings: config.Config, *, completed: bool
) -> list[str]:
    """Drop switches the configured model has caught up with (a kept switch
    is in config.toml by the next run), and, once the plan is `completed`,
    every switch not marked `keep`. An unreadable file is left alone.
    Returns the agents removed."""
    try:
        overrides = read_model_overrides(root)
    except UnreadableModelOverrides:
        return []
    removed = [
        agent
        for agent, override in overrides.items()
        if (completed and not override.keep)
        or settings.models.get(agent) == override.model
    ]
    if removed:
        _write_model_overrides(
            root, {a: o for a, o in overrides.items() if a not in removed}
        )
    return removed


def switch_model(
    root: Path,
    settings: config.Config,
    agent: str,
    model: str,
    *,
    keep: bool = False,
) -> str:
    """Run `agent` on `model` from its next turn, in every role it plays.
    `model` equal to the configured one, or "", removes the switch. With
    `keep`, config.toml gets the model too. The running process is never
    signalled: the loop re-reads the file before every turn."""
    if agent not in settings.agents:
        raise RoleSetError(f"{agent} is not set up for this relay")
    if agent not in settings.model_flags:
        raise RoleSetError(
            f"{agent} has no way to set a model; set model_flag under "
            f"[agents.{agent}]"
        )
    configured = settings.models.get(agent, "")
    overrides = _readable_model_overrides(root)
    if not model or model == configured:
        overrides.pop(agent, None)
        _write_model_overrides(root, overrides)
        return f"{agent} back to {configured or 'its default'} from its next turn."
    overrides[agent] = ModelOverride(
        model=model, since=datetime.now().astimezone().isoformat(), keep=keep
    )
    _write_model_overrides(root, overrides)
    if keep:
        target = config.config_path(root)
        text = target.read_text(encoding="utf-8") if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_upsert_agent_model(text, agent, model), encoding="utf-8")
    return f"{agent} → {model} from its next turn."


def model_status(root: Path, settings: config.Config) -> str:
    """One line per agent running a switched model this run."""
    lines = []
    for agent, override in _readable_model_overrides(root).items():
        if agent not in settings.agents or agent not in settings.model_flags:
            continue
        configured = settings.models.get(agent, "")
        # A kept switch is in config.toml now, so it is no longer a switch.
        if override.model == configured:
            continue
        lines.append(
            f"{agent}: {override.model} (switched from {configured or 'its default'})"
        )
    return "\n".join(lines)


def current_roles(settings: config.Config) -> dict[str, str]:
    """Every role name -> its configured agent, whichever shape is active."""
    if settings.pipeline is not None:
        return {name: role.agent for name, role in settings.pipeline.roles.items()}
    return {
        "implementer": settings.roles.implementer,
        "reviewer": settings.roles.reviewer,
    }


def status(root: Path, settings: config.Config) -> str:
    lines = []
    overrides = failover.read_overrides(root)
    for role, configured in current_roles(settings).items():
        override = overrides.get(role)
        if override is None:
            lines.append(f"{role}: {configured}")
        elif override.reason == failover.MANUAL:
            lines.append(
                f"{role}: {configured}, currently {override.agent} "
                f"(switched by you, since {override.since})"
            )
        else:
            verb = failover.reason_verb(override.reason)
            failed = override.backup_for
            # A switch away from a manual one tried the configured agent too,
            # so it is not stale.
            if failed != configured and configured not in override.tried:
                failed += f" (configured is now {configured})"
            line = (
                f"{role}: {configured}, currently {override.agent} "
                f"({override.reason}: {failed} {verb}, since {override.since})"
            )
            if override.tried and override.tried != [override.backup_for]:
                already = [a for a in override.tried if a != override.agent]
                if already:
                    line += f", tried: {', '.join(already)}"
            lines.append(line)
    if models := model_status(root, settings):
        lines.append(models)
    return "\n".join(lines)


def readiness(root: Path, settings: config.Config, **checks) -> str:
    """One line per agent from failover.ready_agents; `checks` passes its
    which/runner/now through. Prefixed "agent" because a pipeline role may be
    named after an agent."""
    lines = []
    for row in failover.ready_agents(root, settings, **checks):
        state = "ready" if row.ready else f"not ready, {row.why}"
        lines.append(f"agent {row.agent}: {state}")
    return "\n".join(lines)


def reset(root: Path, role: str | None) -> str:
    removed = failover.clear_overrides(root, role)
    if removed == 0:
        return "Nothing to reset." if role is None else f"{role} was not on a backup."
    if role is None:
        return f"Reset {removed} role(s) to their configured agent."
    return f"{role} reset to its configured agent."


def switch(
    root: Path,
    settings: config.Config,
    role: str,
    agent: str,
    *,
    keep: bool = False,
) -> str:
    """Point `role` at `agent` from its next turn, for this run only (or past
    it, with `keep`). Writes only the override file: config.toml is never
    touched and the running process is never signalled, since the loop
    re-reads overrides before every stage."""
    current = current_roles(settings)
    if role not in current:
        configured = ", ".join(sorted(current))
        raise RoleSetError(f"{role!r} is not a configured role ({configured})")
    if agent not in settings.agents:
        raise RoleSetError(f"{agent} is not set up for this relay")
    configured_agent = current[role]
    if agent == configured_agent:
        failover.clear_overrides(root, role)
        return f"{role} back to {agent} from its next turn."
    failover.write_override(
        root,
        role,
        failover.ActiveOverride(
            agent=agent,
            backup_for=configured_agent,
            reason=failover.MANUAL,
            since=datetime.now().astimezone().isoformat(),
            tried=[],
            keep=keep,
        ),
    )
    return f"{role} → {agent} from its next turn."


def _upsert_role(text: str, role: str, agent: str) -> str:
    """Set `role = "agent"` under [roles], creating the table if absent."""
    lines = text.splitlines(keepends=True)
    table_start = None
    table_end = len(lines)
    for index, line in enumerate(lines):
        if table_start is None and line.strip() == "[roles]":
            table_start = index
            continue
        if (
            table_start is not None
            and index > table_start
            and re.match(r"^\[", line.strip())
        ):
            table_end = index
            break

    key_pattern = re.compile(rf"^{re.escape(role)}\s*=")
    if table_start is not None:
        for index in range(table_start + 1, table_end):
            if key_pattern.match(lines[index].strip()):
                lines[index] = f'{role} = "{agent}"\n'
                return "".join(lines)
        if table_end and not lines[table_end - 1].endswith(("\n", "\r")):
            lines[table_end - 1] += "\n"
        lines.insert(table_end, f'{role} = "{agent}"\n')
        return "".join(lines)

    suffix = "" if (not text or text.endswith("\n")) else "\n"
    return text + suffix + f'\n[roles]\n{role} = "{agent}"\n'


def _upsert_agent_model(text: str, name: str, model: str) -> str:
    """Set `model = "..."` under [agents.<name>], creating the block if absent."""
    header = f"[agents.{name}]"
    lines = text.splitlines(keepends=True)
    table_start = None
    table_end = len(lines)
    for index, line in enumerate(lines):
        if table_start is None and line.strip() == header:
            table_start = index
            continue
        if (
            table_start is not None
            and index > table_start
            and re.match(r"^\[", line.strip())
        ):
            table_end = index
            break

    if table_start is not None:
        for index in range(table_start + 1, table_end):
            if re.match(r"^model\s*=", lines[index].strip()):
                lines[index] = f'model = "{model}"\n'
                return "".join(lines)
        if table_end and not lines[table_end - 1].endswith(("\n", "\r")):
            lines[table_end - 1] += "\n"
        lines.insert(table_end, f'model = "{model}"\n')
        return "".join(lines)

    suffix = "" if (not text or text.endswith("\n")) else "\n"
    return text + suffix + f'\n{header}\nmodel = "{model}"\n'


def set_role(
    root: Path,
    settings: config.Config,
    role: str,
    *,
    agent: str | None,
    model: str | None,
    confirm=input,
) -> str:
    """Permanently point `role` at `agent`, optionally selecting its model."""
    current = current_roles(settings)
    if role not in current:
        configured = ", ".join(sorted(current))
        raise RoleSetError(f"{role!r} is not a configured role ({configured})")

    interactive = agent is None
    if agent is None:
        try:
            answer = confirm(f"Agent for {role} [{current[role]}]: ").strip()
        except EOFError:
            raise RoleSetError("no terminal to prompt on; pass --agent") from None
        agent = answer or current[role]

    if agent not in adapters.BUILTIN and agent not in settings.adapters:
        builtins = ", ".join(sorted(adapters.BUILTIN))
        raise RoleSetError(
            f"{agent!r} is not a built-in agent ({builtins}) or a configured "
            "generic agent"
        )

    if interactive and model is None and agent in adapters.BUILTIN:
        preset = whyline_model.read(root).get(agent)
        prompt = (
            f"Model for {agent} [{preset}] (blank to accept, or type another): "
            if preset
            else f"Model for {agent} (blank for default): "
        )
        try:
            answer = confirm(prompt).strip()
        except EOFError:
            answer = ""
        model = answer or preset

    if model is not None and agent not in adapters.BUILTIN:
        builtins = ", ".join(sorted(adapters.BUILTIN))
        raise RoleSetError(
            f"--model only applies to a built-in agent name ({builtins}); "
            f"{agent!r} already carries its own model if it's an alias"
        )

    target = config.config_path(root)
    text = target.read_text(encoding="utf-8")
    text = _upsert_role(text, role, agent)
    if model is not None:
        text = _upsert_agent_model(text, agent, model)
    target.write_text(text, encoding="utf-8")

    summary = f"{role} set to {agent}"
    return summary if model is None else f"{summary} (model {model})"
