import dataclasses
from pathlib import Path
import subprocess
import sys
import pytest

from whyline_relay import config, loop, plan

FAKE = str(Path(__file__).parent / "fake_pipeline_agent.py")
TASK = plan.Task(task_id="T-1", text="T-1: Add the parser", checked=False, line_index=0)


def _task(text="T-1: Add the parser"):
    return plan.Task(task_id="T-1", text=text, checked=False, line_index=0)


def test_commit_message_rules():
    assert loop.commit_message(_task(), "Adds the parser. Also tests.") == "feat: Adds the parser (T-1)"
    assert loop.commit_message(_task("T-1: Fix the crash"), "Guard the None.") == "fix: Guard the None (T-1)"
    assert loop.commit_message(_task(), "docs: explain flags") == "docs: explain flags (T-1)"
    assert len(loop.commit_message(_task(), "x" * 200)) <= len("feat: ") + 72 + len(" (T-1)")
    assert loop.commit_message(_task(), "") == "chore: T-1 needed no changes (T-1)"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "relay/plan")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    (tmp_path / ".whyline").mkdir()
    (tmp_path / ".whyline" / ".gitignore").write_text("active-handoff.json\nledger.jsonl\n")
    (tmp_path / "plan.md").write_text("- [ ] T-1: Add the parser\n")
    git("add", "-A")
    git("commit", "-m", "initial")
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    return tmp_path


def settings_using(codex_spec: str, claude_spec: str, root: Path) -> config.Config:
    base = config.load(root)
    return dataclasses.replace(
        base,
        agents={
            "codex": [sys.executable, FAKE, codex_spec, str(root)],
            "claude": [sys.executable, FAKE, claude_spec, str(root)],
        },
    )


def test_reviewer_approves_without_committing_relay_commits(repo: Path, monkeypatch):
    settings = settings_using("claude:ready-for-review", "claude:approved", repo)
    real_run = loop.agents.run

    def run_then_write(command, prompt, **kwargs):
        code = real_run(command, prompt, **kwargs)
        if "ready-for-review" in " ".join(command):
            (repo / "parser.py").write_text("def parse(): pass\n")
        return code

    monkeypatch.setattr(loop.agents, "run", run_then_write)
    outcomes = loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)
    assert [o.task_id for o in outcomes] == ["T-1"]

    tasks = plan.parse((repo / "plan.md").read_text(encoding="utf-8"))
    assert plan.find(tasks, "T-1").checked

    commit_msg = subprocess.run(
        ["git", "log", "-1", "--format=%s", "HEAD~1"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert commit_msg.endswith("(T-1)")

    files = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD~1"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert files == ["parser.py"]


def test_reviewer_approves_after_committing_no_second_commit(repo: Path):
    base = loop.gitcheck.head_commit(repo)
    settings = settings_using("claude:ready-for-review", "commit:claude:approved", repo)
    outcomes = loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)
    assert [o.task_id for o in outcomes] == ["T-1"]

    tasks = plan.parse((repo / "plan.md").read_text(encoding="utf-8"))
    assert plan.find(tasks, "T-1").checked

    commits = subprocess.run(
        ["git", "log", f"{base}..HEAD", "--format=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().splitlines()

    assert commits == ["chore: tick T-1 in the plan", "feat: x (T-1)"]
    task_commits = [c for c in commits if "(T-1)" in c]
    assert len(task_commits) == 1
    assert task_commits[0] == "feat: x (T-1)"
