"""Backup agents: detecting a usage limit or a lost login, and the sticky switch to a backup."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from whyline_relay import agents, config
from whyline_relay.adapters.base import Adapter
from whyline_relay.adapters import grok

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
    tried: list[str] = field(default_factory=list)
    # Absolute ISO time the usage limit lifts, or "" when the text gave none
    # or the override was written before this field.
    resets_at: str = ""


# "Resets in 158h14m22s" or "Resets in 158h14m". Seconds are optional.
_RESET_IN = re.compile(r"resets in\s+(\d+)h(\d+)m(?:(\d+)s)?", re.IGNORECASE)


def path(root: Path, filename: str = "active-roles.json") -> Path:
    return config.relay_dir(root) / filename


def chat_path(root: Path) -> Path:
    """Chat's own override file -- separate from the pipeline's, since a
    custom pipeline can name a role after an agent (role = "claude"), which
    would collide with chat's key (the agent literally called claude) if
    they shared one file."""
    return path(root, "chat-active-agents.json")


def read_overrides(
    root: Path, storage_path: Path | None = None
) -> dict[str, ActiveOverride]:
    target = storage_path if storage_path is not None else path(root)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    required = ("agent", "backup_for", "reason", "since")
    result: dict[str, ActiveOverride] = {}
    for role, record in raw.items():
        if not isinstance(record, dict) or not all(
            isinstance(record.get(f), str) for f in required
        ):
            continue
        tried = record.get("tried")
        if not isinstance(tried, list) or not all(isinstance(t, str) for t in tried):
            tried = []
        resets_at = record.get("resets_at", "")
        if not isinstance(resets_at, str):
            resets_at = ""
        result[role] = ActiveOverride(
            **{f: record[f] for f in required},
            tried=list(tried),
            resets_at=resets_at,
        )
    return result


def _write_all(
    root: Path,
    overrides: dict[str, ActiveOverride],
    storage_path: Path | None = None,
) -> None:
    target = storage_path if storage_path is not None else path(root)
    if not overrides:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({r: asdict(o) for r, o in overrides.items()}, indent=2) + "\n",
        encoding="utf-8",
    )


def write_override(
    root: Path,
    role: str,
    override: ActiveOverride,
    storage_path: Path | None = None,
) -> None:
    overrides = read_overrides(root, storage_path)
    overrides[role] = override
    _write_all(root, overrides, storage_path)


def clear_overrides(
    root: Path, role: str | None = None, storage_path: Path | None = None
) -> int:
    """Remove the override for `role`, or every override if `role` is None. Returns the count removed."""
    overrides = read_overrides(root, storage_path)
    if role is None:
        removed = len(overrides)
        overrides = {}
    elif role in overrides:
        del overrides[role]
        removed = 1
    else:
        removed = 0
    _write_all(root, overrides, storage_path)
    return removed


def resolve_chat_agent(root: Path, requested: str) -> str:
    """The agent actually addressed right now for `requested`: its backup if
    switched, else `requested` unchanged. Chat's own counterpart to
    effective_agent() -- there is no settings.roles to fall back to here,
    since chat addresses agents by name directly, not by role."""
    override = read_overrides(root, chat_path(root)).get(requested)
    return override.agent if override is not None else requested


def effective_agent(root: Path, settings: config.Config, role: str) -> str:
    """The agent actually filling `role` right now: its backup if switched, else its configured agent."""
    override = read_overrides(root).get(role)
    return override.agent if override is not None else getattr(settings.roles, role)


def pipeline_effective_agent(root: Path, settings: config.Config, role: str) -> str:
    """The agent actually filling a configured pipeline's `role` right now:
    its backup if switched, else the pipeline's own configured agent for it.
    A pipeline role name (e.g. "tester") is not a field on Roles, so this
    cannot reuse effective_agent()'s getattr(settings.roles, role) fallback."""
    override = read_overrides(root).get(role)
    if override is not None:
        return override.agent
    return settings.pipeline.roles[role].agent


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


def classify(
    adapter: Adapter,
    text: str,
    command: list[str],
    runner: Runner = subprocess.run,
) -> str | None:
    """'rate-limit', 'overload', 'auth', or None. Only when a turn produced no handoff.

    Rate-limit is checked before overload, and overload before auth. The auth
    check runs only when `command` is actually the adapter's own binary — the
    same restriction preflight._logins already applies, and for the same reason:
    a stand-in command (a test fixture, or a deliberately different wrapper)
    must never have someone else's login checked against it.
    """
    visible = grok.rate_limit_text(adapter, text, command)
    if agents.rate_limited(visible):
        return "rate-limit"
    if agents.overloaded(visible):
        return "overload"
    checkable = (
        adapter.login_argv is not None
        and bool(command)
        and Path(command[0]).name == adapter.binary
    )
    if checkable and not still_logged_in(adapter, runner):
        return "auth"
    return None


def failover_reason(
    adapter: Adapter,
    text: str,
    command: list[str],
    runner: Runner = subprocess.run,
) -> str | None:
    """'rate-limit', 'auth', or None.

    Overload is deliberately absent. It is a one-turn switch (`classify`
    returns it), and callers of this function — chat in particular — write a
    sticky override for every reason they see. A capacity error clears in
    minutes and must not stick.
    """
    reason = classify(adapter, text, command, runner)
    if reason == "overload":
        return None
    return reason


def reset_deadline(text: str, *, now: datetime | None = None) -> str:
    """ISO time when `Resets in <N>h<M>m` (optional seconds) lifts, or ""."""
    match = _RESET_IN.search(text)
    if match is None:
        return ""
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3) or 0)
    moment = now if now is not None else datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    deadline = moment + timedelta(hours=hours, minutes=minutes, seconds=seconds)
    return deadline.isoformat()


def rate_limited(adapter: Adapter, text: str, command: list[str]) -> bool:
    """Detect quota output without treating Grok's private reasoning as fact."""
    return agents.rate_limited(grok.rate_limit_text(adapter, text, command))


def next_backup(
    chain: list[str], tried: set[str], exclude: frozenset[str] = frozenset()
) -> str | None:
    """The first chain entry not already tried or excluded, or None if the
    chain is exhausted. Callers add the just-failed agent to `tried` before
    calling, so this never returns the agent that just failed."""
    for candidate in chain:
        if candidate not in tried and candidate not in exclude:
            return candidate
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
