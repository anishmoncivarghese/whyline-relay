"""Whether the relay is paused, pausing, or neither, for `status` and the console."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from whyline_relay import config, invocation, loop, roles, running, state

NONE = "none"
PAUSING = "pausing"
PAUSED_BY_USER = "paused_by_user"
CUT_OFF = "cut_off"
PAUSED = "paused"


@dataclass(frozen=True)
class PauseState:
    kind: str
    # When the pause was asked for (pausing) or taken (the paused kinds);
    # None when no time was recorded or it cannot be read.
    since: datetime | None = None
    agent: str = ""
    task_id: str = ""
    round: int = 0
    stage: str = ""
    reason: str = ""


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return (clock or (lambda: datetime.now().astimezone()))()


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return None


def pause_state(
    root: Path, *, clock: Callable[[], datetime] | None = None
) -> PauseState:
    """Where the relay stands on pausing.

    A live relay is never paused: its saved state is the last pause, history.
    It is pausing while PAUSE waits for the current turn to finish. `clock`
    stands in for a request time that cannot be read, so the wait counts
    from now rather than going missing.
    """
    active = running.live(root)
    if active is not None:
        request = loop.pause_request(root)
        if request is None:
            return PauseState(NONE)
        return PauseState(
            PAUSING,
            since=_parse(request.requested_at) or _now(clock),
            agent=active.agent,
            task_id=active.task,
            round=active.round,
            stage=active.stage,
        )
    saved = state.load(root)
    if saved is None:
        return PauseState(NONE)
    common = {
        "since": _parse(saved.paused_at),
        "task_id": saved.task_id,
        "round": saved.round,
        "stage": saved.stage,
        "reason": saved.paused_reason,
    }
    # A user pause taken before a resumed cut-off turn re-ran still carries
    # that cut-off; the user's pause is what happened last, so it wins.
    if saved.paused_by == "user":
        return PauseState(PAUSED_BY_USER, **common)
    if saved.cut_off:
        return PauseState(
            CUT_OFF,
            **{**common, "stage": str(saved.cut_off.get("stage") or saved.stage)},
            agent=str(saved.cut_off.get("agent") or ""),
        )
    return PauseState(PAUSED, **common)


def model_lines(root: Path) -> str:
    """"claude: opus (switched from sonnet)" per switched model; "" when none,
    or when config.toml cannot be read (status still reports the rest)."""
    try:
        settings = config.load(root)
    except config.ConfigError:
        return ""
    return roles.model_status(root, settings)


def _elapsed(seconds: float) -> str:
    minutes, remainder = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {remainder}s"
    return f"{remainder}s"


def pause_line(
    pause: PauseState, *, clock: Callable[[], datetime] | None = None
) -> str | None:
    """The one line `status` prints for `pause`; None when there is no pause."""
    resume = f"Run `{invocation.command('resume')}` to continue."
    where = f"{pause.task_id}, round {pause.round}"
    if pause.kind == PAUSING:
        line = f"Pausing: waiting for {pause.agent} to finish its turn"
        if pause.since is None:
            return line
        now = _now(clock)
        since = pause.since if pause.since.tzinfo else pause.since.astimezone()
        return f"{line} ({_elapsed((now - since).total_seconds())})"
    if pause.kind == PAUSED_BY_USER:
        return f"Paused by you at {where}. {resume}"
    if pause.kind == CUT_OFF:
        owner = f"{pause.agent}'s" if pause.agent else "the agent's"
        turn = f"{owner} {pause.stage} turn" if pause.stage else f"{owner} turn"
        return f"Paused at {where}: {turn} was cut off. {resume}"
    if pause.kind == PAUSED:
        return f"Paused at {where}: {pause.reason.rstrip('.')}. {resume}"
    return None
