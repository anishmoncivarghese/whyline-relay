"""Inspecting and clearing a sticky backup switch."""

from __future__ import annotations

from pathlib import Path

from whyline_relay import config, failover


def status(root: Path, settings: config.Config) -> str:
    lines = []
    for role in ("implementer", "reviewer"):
        configured = getattr(settings.roles, role)
        override = failover.read_overrides(root).get(role)
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
