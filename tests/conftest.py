"""Shared test setup."""

from pathlib import Path
import sys
import types

import pytest

from whyline_relay import cli, preflight


@pytest.fixture(autouse=True)
def no_desktop_notifications(monkeypatch):
    """The CLI announces pauses and completions; a test run must never pop a real one."""
    monkeypatch.setattr(
        cli, "notify", types.SimpleNamespace(send=lambda *args, **kwargs: None)
    )
    # Existing command tests exercise behavior after startup. Keep them hermetic:
    # focused preflight tests install their own results or call the module directly.
    monkeypatch.setattr(
        cli,
        "_launch_checks",
        lambda root, plan_path, *, allow_dirty: [
            preflight.Check("ok", "test preflight")
        ],
    )


@pytest.fixture
def repo_with_git(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def two_role_repo(tmp_path, monkeypatch):
    from whyline_relay import loop
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)

    def factory(plan_text: str) -> Path:
        import subprocess

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "T")
        (tmp_path / "README.md").write_text("x\n")
        (tmp_path / "plan.md").write_text(plan_text)
        relay = tmp_path / ".whyline" / "relay"
        relay.mkdir(parents=True, exist_ok=True)
        fake = str(Path(__file__).parent / "fake_pipeline_agent.py")
        (relay / "config.toml").write_text(
            '[roles]\nimplementer = "codex"\nreviewer = "claude"\n'
            f'[agents.codex]\ncommand = ["{sys.executable}", "{fake}", "claude:ready-for-review", "{tmp_path}"]\n'
            f'[agents.claude]\ncommand = ["{sys.executable}", "{fake}", "commit:claude:approved", "{tmp_path}"]\n'
        )
        (tmp_path / ".whyline" / ".gitignore").write_text("active-handoff.json\nledger.jsonl\n")
        git("add", "-A")
        git("commit", "-m", "initial setup")
        git("checkout", "-b", "relay/plan")
        return tmp_path

    return factory
