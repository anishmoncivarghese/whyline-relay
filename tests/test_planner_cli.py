import subprocess
from pathlib import Path

import pytest

from whyline_relay import cli, planner, state


def make_repo(tmp_path: Path) -> Path:
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "README.md").write_text("x\n")
    subprocess.run(
        ["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / ".whyline" / "relay").mkdir(parents=True)
    (tmp_path / ".whyline" / "relay" / "config.toml").write_text("")
    return tmp_path


def test_plan_with_no_description_and_no_discard_is_an_error(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert cli.main(["plan", "--repo", str(repo)]) == cli.EXIT_ERROR
    assert "description is required" in capsys.readouterr().err


def test_plan_discard_with_nothing_in_progress(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert cli.main(["plan", "--discard", "--repo", str(repo)]) == cli.EXIT_OK
    assert "Nothing to discard" in capsys.readouterr().out


def test_plan_refuses_when_already_in_progress(tmp_path, capsys, monkeypatch):
    repo = make_repo(tmp_path)
    state.save_plan(
        repo,
        state.PlanState(
            description="x",
            stage="draft",
            round=1,
            stage_visits={"draft": 1},
            agent="",
            feedback="",
            draft_path=str(planner.draft_path(repo)),
            paused_reason="",
            log_path="",
        ),
    )
    assert cli.main(["plan", "do a thing", "--repo", str(repo)]) == cli.EXIT_ERROR
    assert "already in progress" in capsys.readouterr().err


def test_resume_dispatches_to_the_planner_when_a_plan_checkpoint_exists(
    tmp_path, capsys, monkeypatch
):
    # planner.resume's own interactive gate is already exercised directly, with
    # an explicit confirm=, by Task 6's tests -- confirm=input is a default
    # bound once at import time, so monkeypatching builtins.input here would
    # not reach it (the same reason tests/test_roles.py and tests/test_init.py
    # always pass confirm= explicitly rather than patching builtins.input).
    # This test proves only cmd_resume's new dispatch branch: a saved PlanState
    # routes to planner.resume instead of falling through to the task-resume path.
    repo = make_repo(tmp_path)
    saved = state.PlanState(
        description="x",
        stage="@complete",
        round=1,
        stage_visits={"draft": 1, "review": 1},
        agent="",
        feedback="",
        draft_path=str(planner.draft_path(repo)),
        paused_reason="",
        log_path="",
    )
    state.save_plan(repo, saved)
    monkeypatch.setattr(
        planner, "resume", lambda root, settings, plan_state: "Discarded. ok"
    )
    assert cli.main(["resume", "--repo", str(repo)]) == cli.EXIT_OK
    assert "Discarded" in capsys.readouterr().out
