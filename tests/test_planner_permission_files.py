"""The planner must not assume `init` ran. The console's Plan comes before
Set up, and claude's managed command names .whyline/relay/claude-settings.json,
so a repo that never ran init used to fail its plan review with "Settings
file not found" -> "claude exited without handing off"."""
import subprocess
from pathlib import Path

import pytest

from whyline_relay import agents, config, loop, planner


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def _draft(root: Path, monkeypatch) -> list[bool]:
    settings_file = root / ".whyline" / "relay" / "claude-settings.json"
    seen: list[bool] = []

    def fake_run(command, prompt, **kwargs):
        seen.append(settings_file.exists())  # then exit without a handoff

    monkeypatch.setattr(agents, "run", fake_run)
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda *a, **k: "")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    with pytest.raises(loop.Paused):
        planner.draft(root, config.load(root), "build it")
    return seen


def test_plan_draft_writes_claude_settings_before_any_agent_runs(repo, monkeypatch):
    head = _git(repo, "rev-parse", "HEAD")
    assert _draft(repo, monkeypatch) == [True]
    assert _git(repo, "rev-parse", "HEAD") == head  # plan commits only plan.md


def test_plan_draft_keeps_a_customized_settings_file(repo, monkeypatch):
    custom = repo / ".whyline" / "relay" / "claude-settings.json"
    custom.parent.mkdir(parents=True)
    custom.write_text('{"mine": true}\n')
    _draft(repo, monkeypatch)
    assert custom.read_text() == '{"mine": true}\n'
