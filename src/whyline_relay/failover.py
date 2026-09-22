"""Backup agents: detecting a usage limit or a lost login, and the sticky switch to a backup."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from whyline_relay import agents, config
from whyline_relay.adapters.base import Adapter

Runner = Callable[..., subprocess.CompletedProcess]

# (verb, suffix) — the plain pause message is "{agent} {verb}; {suffix}", which for
# "rate-limit" reproduces relay 0.2.3's message byte for byte.
REASON_TEXT: dict[str, tuple[str, str]] = {
    "rate-limit": ("hit a usage or rate limit", "try again when it resets"),
    "auth": ("is no longer logged in", "try again once you've signed back in"),
}


@dataclass(frozen=True)
class ActiveOverride:
    agent: str
    backup_for: str
    reason: str  # "rate-limit" | "auth"
    since: str  # ISO timestamp


def path(root: Path) -> Path:
    return config.relay_dir(root) / "active-roles.json"


def read_overrides(root: Path) -> dict[str, ActiveOverride]:
    try:
        raw = json.loads(path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    fields = ("agent", "backup_for", "reason", "since")
    result: dict[str, ActiveOverride] = {}
    for role, record in raw.items():
        if isinstance(record, dict) and all(
            isinstance(record.get(f), str) for f in fields
        ):
            result[role] = ActiveOverride(**{f: record[f] for f in fields})
    return result


def _write_all(root: Path, overrides: dict[str, ActiveOverride]) -> None:
    target = path(root)
    if not overrides:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({r: asdict(o) for r, o in overrides.items()}, indent=2) + "\n",
        encoding="utf-8",
    )


def write_override(root: Path, role: str, override: ActiveOverride) -> None:
    overrides = read_overrides(root)
    overrides[role] = override
    _write_all(root, overrides)


def clear_overrides(root: Path, role: str | None = None) -> int:
    """Remove the override for `role`, or every override if `role` is None. Returns the count removed."""
    overrides = read_overrides(root)
    if role is None:
        removed = len(overrides)
        overrides = {}
    elif role in overrides:
        del overrides[role]
        removed = 1
    else:
        removed = 0
    _write_all(root, overrides)
    return removed


def effective_agent(root: Path, settings: config.Config, role: str) -> str:
    """The agent actually filling `role` right now: its backup if switched, else its configured agent."""
    override = read_overrides(root).get(role)
    return override.agent if override is not None else getattr(settings.roles, role)


def still_logged_in(adapter: Adapter, runner: Runner = subprocess.run) -> bool:
    """True when the adapter has no login check, or the check says it's fine.

    Never guesses a "no" from an error or a timeout: those mean the check was
    inconclusive, not that the agent is logged out.
    """
    if adapter.login_argv is None:
        return True
    try:
        result = runner(
            list(adapter.login_argv), capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return result.returncode == 0


def failover_reason(
    adapter: Adapter,
    text: str,
    command: list[str],
    runner: Runner = subprocess.run,
) -> str | None:
    """'rate-limit', 'auth', or None. Checked only when a turn produced no handoff.

    The auth check runs only when `command` is actually the adapter's own binary —
    the same restriction preflight._logins already applies, and for the same reason:
    a stand-in command (a test fixture, or a deliberately different wrapper) must never
    have someone else's login checked against it.
    """
    if agents.rate_limited(text):
        return "rate-limit"
    checkable = (
        adapter.login_argv is not None and Path(command[0]).name == adapter.binary
    )
    if checkable and not still_logged_in(adapter, runner):
        return "auth"
    return None


def pause_message(
    agent: str,
    role: str,
    reason: str,
    override: ActiveOverride | None,
) -> str:
    verb, suffix = REASON_TEXT[reason]
    if override is not None and override.agent == agent:
        other_verb, _ = REASON_TEXT[override.reason]
        return (
            f"{agent} (backup for {role}; {override.backup_for} was already out: "
            f"it {other_verb}) also {verb}; {suffix}"
        )
    return f"{agent} {verb}; {suffix}"
