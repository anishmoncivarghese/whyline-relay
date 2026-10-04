"""Shared test setup."""

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
