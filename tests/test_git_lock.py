"""A brief index lock is waited out; a commit that still fails pauses the task
approved, and resume finishes it without running a stage."""

import subprocess
from pathlib import Path

import pytest

from whyline_relay import gitcheck, handoff, loop, state
from test_loop_pipeline import (  # noqa: F401
    _scripted_run,
    _task_commit,
    make_pipeline,
    repo,
    settings_with_pipeline,
)

HINT = "(another git program held the index lock for 8 s; close it, then resume)"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _lock(root: Path) -> Path:
    lock = root / ".git" / "index.lock"
    lock.write_text("")
    return lock


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    waited: list[float] = []
    monkeypatch.setattr(gitcheck.time, "sleep", waited.append)
    return waited


def test_a_lock_that_clears_on_the_third_try_commits(repo, monkeypatch):
    lock = _lock(repo)
    (repo / "feature.txt").write_text("x\n")
    waited: list[float] = []

    def sleep(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 2:
            lock.unlink()

    monkeypatch.setattr(gitcheck.time, "sleep", sleep)
    assert gitcheck.commit_all(repo, "feat: x (T-1)") is True
    assert waited == [0.25, 0.5]
    assert _git(repo, "log", "-1", "--format=%s") == "feat: x (T-1)"


def test_a_lock_that_never_clears_raises_with_the_hint(repo, sleeps):
    _lock(repo)
    (repo / "feature.txt").write_text("x\n")
    with pytest.raises(gitcheck.GitError) as raised:
        gitcheck.commit_all(repo, "feat: x (T-1)")
    message = str(raised.value)
    assert message.startswith("git add -A failed: ")
    assert "index.lock': File exists" in message
    assert message.endswith(HINT)
    assert sleeps == [0.25, 0.5, 1, 2, 2, 2]


def test_an_injected_sleep_replaces_time_sleep(repo, sleeps):
    _lock(repo)
    (repo / "feature.txt").write_text("x\n")
    injected: list[float] = []
    with pytest.raises(gitcheck.GitError):
        gitcheck._git(repo, "add", "-A", sleep=injected.append)
    assert injected == list(gitcheck.LOCK_WAITS)
    assert sleeps == []


def test_a_failure_that_is_not_the_lock_is_not_retried(repo, sleeps):
    with pytest.raises(gitcheck.GitError) as raised:
        gitcheck._git(repo, "checkout", "no-such-branch")
    assert HINT not in str(raised.value)
    assert sleeps == []


def test_read_only_calls_run_without_optional_locks(repo, monkeypatch):
    seen: list[tuple[str, dict | None]] = []
    real_run = subprocess.run

    def run(command, **kwargs):
        seen.append((command[1], kwargs.get("env")))
        return real_run(command, **kwargs)

    monkeypatch.setattr(gitcheck.subprocess, "run", run)
    gitcheck.is_dirty(repo)
    head = gitcheck.head_commit(repo)
    gitcheck.commit_message(repo, head)
    gitcheck._git(repo, "diff", "--name-only")
    gitcheck.commit_stat(repo)
    gitcheck.commit_empty(repo, "chore: empty")
    read = {name: env for name, env in seen if name != "commit"}
    assert set(read) == {"status", "rev-parse", "log", "diff", "show"}
    assert all(env["GIT_OPTIONAL_LOCKS"] == "0" for env in read.values())
    assert dict(seen)["commit"] is None


PLAN = "# Plan\n\n- [ ] T-1: build it\n"


@pytest.fixture
def planned(repo) -> Path:
    (repo / "plan.md").write_text(PLAN)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "plan")
    return repo


def _run_plan(root: Path, *, resume: bool = False):
    return loop.run_plan(
        root,
        settings_with_pipeline(root, make_pipeline()),
        root / "plan.md",
        branch="main",
        resume=resume,
        echo=False,
    )


def _locked_once(monkeypatch, name: str, when=lambda *a: True):
    """Make gitcheck.`name` meet a held index lock the first time `when` holds."""
    real = getattr(gitcheck, name)
    fired = {"done": False}

    def locked(root, *args):
        if not fired["done"] and when(*args):
            fired["done"] = True
            _lock(root)
        return real(root, *args)

    monkeypatch.setattr(gitcheck, name, locked)


def _never_run(monkeypatch) -> list[str]:
    called: list[str] = []
    monkeypatch.setattr(loop.agents, "run", lambda *a, **k: called.append("ran"))
    return called


def test_a_commit_failure_after_approval_pauses_and_resume_commits(
    planned, monkeypatch, sleeps
):
    base = _git(planned, "rev-parse", "HEAD")
    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted_run(["write:claude:ready", "claude:passed", "claude:approved"]),
    )
    _locked_once(monkeypatch, "commit_all")
    with pytest.raises(loop.Paused) as paused:
        _run_plan(planned)

    reason = paused.value.reason
    assert reason.startswith("T-1: approved, but the commit failed: git add -A failed:")
    assert reason.endswith(f"{HINT}. Resume commits it.")
    saved = state.load(planned)
    assert saved is not None
    assert (saved.task_id, saved.stage, saved.base_commit) == ("T-1", "@complete", base)
    assert saved.paused_reason == reason
    record = handoff.read(planned)
    assert (record.task, record.status) == ("T-1", "approved")
    assert _git(planned, "rev-parse", "HEAD") == base

    (planned / ".git" / "index.lock").unlink()
    called = _never_run(monkeypatch)
    runner_calls: list[object] = []
    outcomes = loop.run_plan(
        planned,
        settings_with_pipeline(planned, make_pipeline()),
        planned / "plan.md",
        branch="main",
        resume=True,
        echo=False,
        runner=lambda *a, **k: runner_calls.append(a),
    )
    assert [outcome.task_id for outcome in outcomes] == ["T-1"]
    assert called == []
    assert runner_calls == []
    sha = _task_commit(planned, "T-1")
    assert "feature.txt" in _git(planned, "show", "--name-only", "--format=", sha)
    assert "- [x] T-1" in (planned / "plan.md").read_text()
    assert state.load(planned) is None


def test_a_tick_commit_failure_pauses_and_resume_only_ticks(
    planned, monkeypatch, sleeps
):
    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted_run(["write:claude:ready", "claude:passed", "claude:approved"]),
    )
    _locked_once(
        monkeypatch, "commit_paths", lambda paths, message: message.startswith("chore: tick")
    )
    with pytest.raises(loop.Paused) as paused:
        _run_plan(planned)

    reason = paused.value.reason
    assert reason.startswith("T-1: approved and committed, but the plan tick commit failed:")
    assert reason.endswith(f"{HINT}. Resume ticks it.")
    task_commit = _task_commit(planned, "T-1")
    assert _git(planned, "rev-parse", "HEAD") == task_commit
    assert "- [ ] T-1" in (planned / "plan.md").read_text()
    saved = state.load(planned)
    assert saved is not None
    assert (saved.task_id, saved.stage) == ("T-1", loop.TICKING)

    (planned / ".git" / "index.lock").unlink()
    assert _git(planned, "status", "--porcelain") == ""
    called = _never_run(monkeypatch)
    outcomes = _run_plan(planned, resume=True)
    assert [outcome.task_id for outcome in outcomes] == ["T-1"]
    assert called == []
    assert _git(planned, "log", "-1", "--format=%s") == "chore: tick T-1 in the plan"
    assert _git(planned, "rev-parse", "HEAD~1") == task_commit
    assert "- [x] T-1" in (planned / "plan.md").read_text()
    assert _git(planned, "status", "--porcelain") == ""
