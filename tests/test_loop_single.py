import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from whyline_relay import config, loop, plan

FAKE = str(Path(__file__).parent / "fake_agent.py")
TASK = plan.Task(task_id="WL-1", text="WL-1: Add the cache", checked=False, line_index=0)


def settings_using(codex_mode: str, claude_mode: str, root: Path) -> config.Config:
    base = config.load(root)
    return config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, FAKE, codex_mode, str(root)],
            "claude": [sys.executable, FAKE, claude_mode, str(root)],
        },
        status_map=base.status_map,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "relay/plan")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-m", "initial")
    (tmp_path / ".whyline").mkdir()
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    return tmp_path


def commit_for_task(root: Path) -> None:
    (root / "feature.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: cache (WL-1)"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_implement_then_review_then_approve(repo: Path, monkeypatch):
    settings = settings_using("review", "approve", repo)
    real_run = loop.agents.run

    def run_then_commit(command, prompt, **kwargs):
        code = real_run(command, prompt, **kwargs)
        if "approve" in command:
            commit_for_task(repo)
        return code

    monkeypatch.setattr(loop.agents, "run", run_then_commit)
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert outcome.committed is True
    assert outcome.rounds == 1


def test_agent_that_writes_no_handoff_pauses(repo: Path):
    settings = settings_using("silent", "approve", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "without handing off" in raised.value.reason


def test_live_marker_describes_the_turn_and_is_removed_afterward(
    repo: Path, monkeypatch
):
    seen: dict = {}

    def inspect_marker(*args, **kwargs):
        marker = json.loads(
            (repo / ".whyline" / "relay" / "running.json").read_text()
        )
        seen.update(marker)
        raise KeyboardInterrupt

    monkeypatch.setattr(loop.agents, "run", inspect_marker)
    with pytest.raises(KeyboardInterrupt):
        loop.run_task(
            repo,
            settings_using("silent", "approve", repo),
            TASK,
            base_commit=loop.gitcheck.head_commit(repo),
            echo=False,
        )

    assert seen["agent"] == "codex"
    assert seen["task"] == "WL-1"
    assert seen["round"] == 1
    assert seen["pid"] == os.getpid()
    assert datetime.fromisoformat(seen["started"]).tzinfo is not None
    assert not (repo / ".whyline" / "relay" / "running.json").exists()


def test_live_marker_is_updated_for_each_agent_turn(repo: Path, monkeypatch):
    settings = settings_using("review", "approve", repo)
    real_run = loop.agents.run
    seen: list[tuple[str, int]] = []

    def inspect_each_turn(command, prompt, **kwargs):
        marker = json.loads(
            (repo / ".whyline" / "relay" / "running.json").read_text()
        )
        seen.append((marker["agent"], marker["round"]))
        code = real_run(command, prompt, **kwargs)
        if "approve" in command:
            commit_for_task(repo)
        return code

    monkeypatch.setattr(loop.agents, "run", inspect_each_turn)
    loop.run_task(
        repo,
        settings,
        TASK,
        base_commit=loop.gitcheck.head_commit(repo),
        echo=False,
    )

    assert seen == [("codex", 1), ("claude", 1)]
    assert not (repo / ".whyline" / "relay" / "running.json").exists()


def test_blocked_handoff_pauses(repo: Path):
    settings = settings_using("blocked", "approve", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "blocked" in raised.value.reason


def test_unknown_status_pauses(repo: Path):
    settings = settings_using("weird", "approve", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "banana" in raised.value.reason


def test_approved_without_a_commit_pauses(repo: Path):
    settings = settings_using("review", "approve", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "no commit" in raised.value.reason.lower()


def test_rate_limited_agent_pauses_with_a_useful_reason(repo: Path):
    settings = settings_using("ratelimited", "approve", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "limit" in raised.value.reason.lower()


def test_logs_are_written_per_round_and_agent(repo: Path, monkeypatch):
    settings = settings_using("review", "approve", repo)
    real_run = loop.agents.run

    def run_then_commit(command, prompt, **kwargs):
        code = real_run(command, prompt, **kwargs)
        if "approve" in command:
            commit_for_task(repo)
        return code

    monkeypatch.setattr(loop.agents, "run", run_then_commit)
    base = loop.gitcheck.head_commit(repo)
    loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    logs = sorted(p.name for p in (repo / ".whyline" / "relay" / "logs").iterdir())
    assert logs == ["WL-1-1-claude.log", "WL-1-1-codex.log"]


def test_the_round_cap_stops_a_review_that_never_approves(repo: Path):
    settings = settings_using("review", "changes", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "3-round cap" in raised.value.reason
    logs = list((repo / ".whyline" / "relay" / "logs").iterdir())
    assert len(logs) == 6


def test_output_that_only_mentions_a_rate_limit_does_not_discard_a_handoff(
    repo: Path, monkeypatch
):
    """An agent writing an HTTP client prints 'Too Many Requests' and still finishes."""
    settings = settings_using("review", "approve", repo)
    chatty = (
        "print('handling HTTP 429 Too Many Requests'); import runpy; "
        f"runpy.run_path({FAKE!r}, run_name='__main__')"
    )
    codex = [sys.executable, "-c", chatty, "review", str(repo)]
    settings = replace(settings, agents={**settings.agents, "codex": codex})
    real_run = loop.agents.run

    def run_then_commit(command, prompt, **kwargs):
        code = real_run(command, prompt, **kwargs)
        if "approve" in command:
            commit_for_task(repo)
        return code

    monkeypatch.setattr(loop.agents, "run", run_then_commit)
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert outcome.committed is True


def test_the_relays_own_logs_stay_out_of_the_reviewers_commit(repo: Path, monkeypatch):
    settings = settings_using("review", "approve", repo)
    real_run = loop.agents.run

    def run_then_commit(command, prompt, **kwargs):
        code = real_run(command, prompt, **kwargs)
        if "approve" in command:
            commit_for_task(repo)
        return code

    monkeypatch.setattr(loop.agents, "run", run_then_commit)
    base = loop.gitcheck.head_commit(repo)
    loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    committed = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout
    assert "feature.py" in committed
    assert "relay/" not in committed


def test_a_handoff_for_another_task_pauses(repo: Path):
    settings = settings_using("wrongtask", "approve", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "OTHER-1" in raised.value.reason


def write_handoff(root: Path, task: str, to_actor: str, status: str, summary: str = "") -> None:
    (root / ".whyline").mkdir(exist_ok=True)
    (root / ".whyline" / "active-handoff.json").write_text(
        json.dumps(
            {
                "v": 1,
                "id": "prior1",
                "type": "Handoff",
                "task": task,
                "from_actor": "x",
                "to_actor": to_actor,
                "status": status,
                "summary": summary,
            }
        )
    )


def watch_agents(monkeypatch, repo: Path, *, commit_on_approve: bool):
    """Record which agent ran and with what prompt; optionally commit like a reviewer."""
    seen: list[tuple[str, str]] = []
    real_run = loop.agents.run

    def spy(command, prompt, **kwargs):
        seen.append(("claude" if "approve" in command else "codex", prompt))
        code = real_run(command, prompt, **kwargs)
        if commit_on_approve and "approve" in command:
            commit_for_task(repo)
        return code

    monkeypatch.setattr(loop.agents, "run", spy)
    return seen


def test_resume_goes_straight_to_the_review_after_a_handoff(repo: Path, monkeypatch):
    write_handoff(repo, "WL-1", "claude", "ready-for-review")
    seen = watch_agents(monkeypatch, repo, commit_on_approve=True)
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(
        repo, settings_using("review", "approve", repo), TASK,
        base_commit=base, echo=False, resume=True,
    )
    assert [agent for agent, _ in seen] == ["claude"]
    assert outcome.committed is True


def test_resume_after_changes_requested_carries_the_feedback(repo: Path, monkeypatch):
    write_handoff(repo, "WL-1", "codex", "changes-requested", "fix the cache key")
    seen = watch_agents(monkeypatch, repo, commit_on_approve=True)
    base = loop.gitcheck.head_commit(repo)
    loop.run_task(
        repo, settings_using("review", "approve", repo), TASK,
        base_commit=base, echo=False, resume=True, start_round=2,
    )
    assert seen[0][0] == "codex"
    assert "fix the cache key" in seen[0][1]


def test_resume_of_an_approved_task_verifies_without_running_an_agent(
    repo: Path, monkeypatch
):
    base = loop.gitcheck.head_commit(repo)
    commit_for_task(repo)
    write_handoff(repo, "WL-1", "claude", "approved")
    seen = watch_agents(monkeypatch, repo, commit_on_approve=False)
    outcome = loop.run_task(
        repo, settings_using("review", "approve", repo), TASK,
        base_commit=base, echo=False, resume=True,
    )
    assert seen == []
    assert outcome.committed is True


def test_resume_ignores_a_handoff_left_by_another_task(repo: Path, monkeypatch):
    write_handoff(repo, "WL-9", "claude", "ready-for-review")
    seen = watch_agents(monkeypatch, repo, commit_on_approve=True)
    base = loop.gitcheck.head_commit(repo)
    loop.run_task(
        repo, settings_using("review", "approve", repo), TASK,
        base_commit=base, echo=False, resume=True,
    )
    assert seen[0][0] == "codex"


def test_a_commit_by_the_implementer_pauses(repo: Path):
    """Codex's sandbox does not block git commit, so the relay must notice."""
    settings = settings_using("codexcommit", "approve", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "forbids" in raised.value.reason
    assert f"git reset {base[:12]}" in raised.value.reason


def test_a_denied_reviewer_says_what_it_was_denied(repo: Path):
    settings = settings_using("review", "denied", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    reason = raised.value.reason
    assert "without handing off" in reason
    assert "git commit -m x" in reason
    assert "claude-settings.json" in reason


def test_a_silent_agent_pause_quotes_its_last_output_line(repo: Path):
    settings = settings_using("silent", "approve", repo)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "its last output was" in raised.value.reason
    assert "fake-agent silent" in raised.value.reason
