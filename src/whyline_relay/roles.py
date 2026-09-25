"""Inspecting a role's agent, clearing a sticky switch, and setting it."""

from __future__ import annotations

import re
from pathlib import Path

from whyline_relay import adapters, config, failover, whyline_model


class RoleSetError(ValueError):
    """A `roles set` request cannot be honored as given."""


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
        else:
            verb, _ = failover.REASON_TEXT[override.reason]
            lines.append(
                f"{role}: {configured}, currently {override.agent} "
                f"({override.reason}: {configured} {verb}, since {override.since})"
            )
    return "\n".join(lines)


def reset(root: Path, role: str | None) -> str:
    removed = failover.clear_overrides(root, role)
    if removed == 0:
        return "Nothing to reset." if role is None else f"{role} was not on a backup."
    if role is None:
        return f"Reset {removed} role(s) to their configured agent."
    return f"{role} reset to its configured agent."


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
