import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from whyline_relay import cli, loop, state, status

NOW = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)


def clock() -> datetime:
    return NOW


def _write_running(root: Path, *, pid: int, agent: str = "codex") -> None:
    target = root / ".whyline" / "relay" / "running.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "agent": agent,
                "task": "RPZ-2",
                "round": 2,
                "started": (NOW - timedelta(minutes=5)).isoformat(),
                "pid": pid,
                "role": "reviewer",
                "stage": "review",
            }
        )
    )


def _save(root: Path, **fields) -> None:
    record = {
        "plan": "plan.md",
        "branch": "relay/plan",
        "task_id": "RPZ-2",
        "round": 2,
        "base_commit": "abc",
        "paused_reason": "codex exited without handing off",
        "log_path": "",
        "stage": "draft",
        "paused_at": (NOW - timedelta(minutes=3)).isoformat(),
    }
    state.save(root, state.RelayState(**{**record, **fields}))


def test_none_with_nothing_saved(tmp_path: Path):
    assert status.pause_state(tmp_path, clock=clock) == status.PauseState("none")
    assert status.pause_line(status.pause_state(tmp_path)) is None


def test_none_while_a_relay_runs_without_a_pause_request(tmp_path: Path):
    _write_running(tmp_path, pid=os.getpid())
    # The saved pause is history while a relay runs.
    _save(tmp_path, paused_by="user")

    assert status.pause_state(tmp_path, clock=clock).kind == "none"


def test_pausing_while_a_relay_runs_and_pause_exists(tmp_path: Path):
    _write_running(tmp_path, pid=os.getpid())
    loop.request_pause(tmp_path, clock=lambda: NOW - timedelta(seconds=80))

    pause = status.pause_state(tmp_path, clock=clock)

    assert pause == status.PauseState(
        "pausing",
        since=NOW - timedelta(seconds=80),
        agent="codex",
        task_id="RPZ-2",
        round=2,
        stage="review",
    )
    assert status.pause_line(pause, clock=clock) == (
        "Pausing: waiting for codex to finish its turn (1m 20s)"
    )


def test_pausing_with_an_unreadable_request_counts_from_now(tmp_path: Path):
    _write_running(tmp_path, pid=os.getpid())
    loop.pause_path(tmp_path).write_text("garbled")

    pause = status.pause_state(tmp_path, clock=clock)

    assert pause.kind == "pausing"
    assert pause.since == NOW
    assert status.pause_line(pause, clock=clock) == (
        "Pausing: waiting for codex to finish its turn (0s)"
    )


def test_a_pause_request_with_no_live_relay_is_not_pausing(tmp_path: Path):
    _write_running(tmp_path, pid=2**31 - 1)
    loop.request_pause(tmp_path)

    assert status.pause_state(tmp_path, clock=clock).kind == "none"


def test_paused_by_user(tmp_path: Path):
    _save(tmp_path, paused_by="user", paused_reason=loop.PAUSED_BY_USER)

    pause = status.pause_state(tmp_path, clock=clock)

    assert pause == status.PauseState(
        "paused_by_user",
        since=NOW - timedelta(minutes=3),
        task_id="RPZ-2",
        round=2,
        stage="draft",
        reason="paused by you",
    )
    assert status.pause_line(pause) == (
        "Paused by you at RPZ-2, round 2. "
        "Run `whyline-relay resume` to continue."
    )


def test_cut_off(tmp_path: Path):
    _save(
        tmp_path,
        paused_reason="paused now by you; codex's turn was cut off",
        cut_off={
            "stage": "review",
            "role": "reviewer",
            "agent": "codex",
            "changed_files": ["src/a.py"],
        },
    )

    pause = status.pause_state(tmp_path, clock=clock)

    assert pause.kind == "cut_off"
    assert (pause.agent, pause.stage, pause.task_id, pause.round) == (
        "codex", "review", "RPZ-2", 2,
    )
    assert status.pause_line(pause) == (
        "Paused at RPZ-2, round 2: codex's review turn was cut off. "
        "Run `whyline-relay resume` to continue."
    )


def test_a_user_pause_wins_over_a_carried_cut_off(tmp_path: Path):
    _save(tmp_path, paused_by="user", cut_off={"stage": "review", "agent": "codex"})

    assert status.pause_state(tmp_path, clock=clock).kind == "paused_by_user"


def test_paused_for_any_other_reason(tmp_path: Path):
    _save(tmp_path, paused_at="")

    pause = status.pause_state(tmp_path, clock=clock)

    assert pause == status.PauseState(
        "paused",
        since=None,
        task_id="RPZ-2",
        round=2,
        stage="draft",
        reason="codex exited without handing off",
    )
    assert status.pause_line(pause) == (
        "Paused at RPZ-2, round 2: codex exited without handing off. "
        "Run `whyline-relay resume` to continue."
    )


@pytest.mark.parametrize(
    ("fields", "line"),
    [
        (
            {"paused_by": "user"},
            "Paused by you at RPZ-2, round 2. Run `whyline-relay resume` to continue.",
        ),
        (
            {"cut_off": {"stage": "review", "agent": "codex"}},
            "Paused at RPZ-2, round 2: codex's review turn was cut off. "
            "Run `whyline-relay resume` to continue.",
        ),
        (
            {},
            "Paused at RPZ-2, round 2: codex exited without handing off. "
            "Run `whyline-relay resume` to continue.",
        ),
    ],
)
def test_status_command_prints_the_paused_line(
    tmp_path: Path, capsys, fields: dict, line: str
):
    _save(tmp_path, **fields)

    assert cli.main(["status", "--repo", str(tmp_path)]) == cli.EXIT_OK

    out = capsys.readouterr().out.splitlines()
    assert out[0] == line
    assert "Resume with: whyline-relay resume" in out


def test_status_command_prints_pausing_under_the_running_line(
    tmp_path: Path, capsys
):
    _write_running(tmp_path, pid=os.getpid())
    loop.request_pause(tmp_path)

    assert cli.main(["status", "--repo", str(tmp_path)]) == cli.EXIT_OK

    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Running: codex reviewing RPZ-2")
    assert out[1].startswith("Pausing: waiting for codex to finish its turn (")


def test_status_command_prints_no_pause_line_when_none(tmp_path: Path, capsys):
    assert cli.main(["status", "--repo", str(tmp_path)]) == cli.EXIT_OK
    assert capsys.readouterr().out == "No relay run in progress.\n"
