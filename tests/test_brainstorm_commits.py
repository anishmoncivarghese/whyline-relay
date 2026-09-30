"""Brainstorm turns commit only brainstorm files.

Observed 2026-09-30 in a real repository: a user ran a brainstorm while
other work was uncommitted, and every turn's `git add -A` swept that work
into commits titled "brainstorm: <agent> independent research on ...". A
brainstorm writes its notes, the shared document and its tracking files;
nothing else in the tree is its business.
"""

import json
import subprocess
from pathlib import Path

from whyline_relay import brainstorm, chat, config
from whyline_relay.agents import RunResult

TOPIC = "retry policy"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def _repo(root: Path) -> Path:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "app.py").write_text("print('v1')\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    # the user's unrelated, uncommitted work
    (root / "app.py").write_text("print('v2 -- half done')\n")
    (root / "notes.txt").write_text("scratch\n")
    return root


def _unrelated_still_uncommitted(root: Path) -> None:
    status = _git(root, "status", "--porcelain")
    assert " M app.py" in status
    assert "?? notes.txt" in status
    assert _git(root, "show", "HEAD:app.py") == "print('v1')\n"
    setup = _git(root, "rev-list", "--max-parents=0", "HEAD").strip()
    since_setup = {
        line.strip()
        for line in _git(root, "log", "--name-only", "--format=", f"{setup}..HEAD").splitlines()
        if line.strip()
    }
    assert "app.py" not in since_setup and "notes.txt" not in since_setup


def _agent(root: Path, models, phase: dict):
    """A fake agent that writes what each brainstorm stage asks for."""

    def run_fn(command, prompt, **kwargs):
        agent = kwargs.get("agent_name") or command[0]
        if phase["name"] == "research":
            target = brainstorm.temp_path(root, agent)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"research by {agent}\n")
        elif phase["name"] == "review":
            shared = brainstorm.shared_path(root, TOPIC)
            shared.write_text(shared.read_text() + f"\nreviewed by {agent}\n")
        elif phase["name"] == "final":
            shared = brainstorm.shared_path(root, TOPIC)
            shared.write_text(shared.read_text() + "\n## Final Synthesis\n\nthe answer\n")
        # an agent that also scribbles outside its brief must not get that committed
        (root / "stray.txt").write_text(f"{agent} was here\n")
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    return run_fn


def test_every_brainstorm_stage_commits_only_brainstorm_files(tmp_path: Path):
    root = _repo(tmp_path)
    settings = config.load(root)
    models = [("claude", "Claude"), ("codex", "Codex")]
    phase = {"name": "research"}
    run_fn = _agent(root, models, phase)
    quiet = lambda *a, **k: None

    actual = brainstorm.run_pass_zero(root, models, TOPIC, settings=settings, run_fn=run_fn, print_fn=quiet)
    _unrelated_still_uncommitted(root)

    brainstorm.merge_pass_zero(root, models, TOPIC, actual_agents=actual)
    _unrelated_still_uncommitted(root)
    shared = brainstorm.shared_path(root, TOPIC)
    assert _git(root, "show", f"HEAD:{shared.relative_to(root).as_posix()}").startswith("# Brainstorm")

    phase["name"] = "review"
    actual = brainstorm.run_review_pass(
        root, models, TOPIC, 1, settings=settings, run_fn=run_fn, print_fn=quiet,
        actual_agents=actual,
    )
    _unrelated_still_uncommitted(root)
    assert "reviewed by codex" in _git(root, "show", f"HEAD:{shared.relative_to(root).as_posix()}")

    phase["name"] = "final"
    brainstorm.run_final_synthesis(
        root, "claude", models, TOPIC, settings=settings, run_fn=run_fn, print_fn=quiet,
        actual_agents=actual,
    )
    _unrelated_still_uncommitted(root)
    assert "## Final Synthesis" in _git(root, "show", f"HEAD:{shared.relative_to(root).as_posix()}")
    # the agent's stray file was never committed either
    assert "?? stray.txt" in _git(root, "status", "--porcelain")
    # and every brainstorm commit touched brainstorm paths only
    for sha in _git(root, "log", "--format=%H", "-n", "20").split():
        message = _git(root, "log", "-1", "--format=%s", sha)
        if not message.startswith("brainstorm:"):
            continue
        for path in _git(root, "show", "--name-only", "--format=", sha).split():
            assert "brainstorm" in path, (message, path)


def test_ordinary_chat_turns_still_commit_the_agents_work(tmp_path: Path):
    """Out of scope on purpose: a chat turn's edits *are* the agent's work."""
    root = _repo(tmp_path)

    def run_fn(command, prompt, **kwargs):
        (root / "feature.py").write_text("x = 1\n")
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    chat.run_turn(root, agent="claude", prompt="add a feature", run_fn=run_fn)
    assert "feature.py" in _git(root, "show", "--name-only", "--format=", "HEAD")


def test_commit_paths_skips_paths_that_never_existed(tmp_path: Path):
    from whyline_relay import gitcheck

    root = _repo(tmp_path)
    (root / "a.md").write_text("a\n")
    committed = gitcheck.commit_paths(root, [root / "a.md", root / "never-written.md"], "m")
    assert committed is True
    assert _git(root, "show", "--name-only", "--format=", "HEAD").split() == ["a.md"]
    assert gitcheck.commit_paths(root, [root / "never-written.md"], "m") is False
