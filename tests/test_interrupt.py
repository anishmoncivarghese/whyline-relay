"""Ctrl+C must stop the running agent and leave state that `resume` can use."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from whyline_relay import cli, config, loop, state

FAKE = str(Path(__file__).parent / "fake_agent.py")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    (tmp_path / "plan.md").write_text("- [ ] WL-1: One\n")
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    # An agent that records its pid and then hangs. Recording the pid, rather than
    # listing processes with pgrep, keeps the test able to fail inside a sandbox
    # that cannot list processes (Codex's cannot: "Cannot get process list").
    hang = f"import os, time; open({str(tmp_path / 'agent.pid')!r}, 'w').write(str(os.getpid())); time.sleep(600)"
    (relay / "config.toml").write_text(
        f'[agents.codex]\ncommand = ["{sys.executable}", "-c", {json.dumps(hang)}]\n'
        f'[agents.claude]\ncommand = ["{sys.executable}", "{FAKE}", "approve", "{tmp_path}"]\n'
    )
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "initial")
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    return tmp_path


def test_run_plan_saves_state_when_interrupted(repo: Path, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(loop, "run_task", interrupted)
    with pytest.raises(KeyboardInterrupt):
        loop.run_plan(
            repo, config.load(repo), repo / "plan.md", branch="relay/plan", echo=False
        )
    saved = state.load(repo)
    assert saved.task_id == "WL-1"
    assert "interrupted" in saved.paused_reason


def test_ctrl_c_stops_the_agent_process_and_saves_state(repo: Path):
    pid_file = repo / "agent.pid"
    previous = signal.getsignal(signal.SIGINT)
    timer = threading.Timer(2.0, os.kill, (os.getpid(), signal.SIGINT))
    timer.start()
    try:
        code = cli.main(["start", "--repo", str(repo)])
    finally:
        timer.cancel()
        signal.signal(signal.SIGINT, previous)
    assert pid_file.exists(), "the agent never started, so this test proved nothing"
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)  # raises if the process is gone
    except ProcessLookupError:
        outlived = False
    else:
        outlived = True
        os.kill(pid, signal.SIGKILL)  # never leave an orphan behind
    assert code == cli.EXIT_PAUSED
    assert not outlived, "the agent outlived Ctrl+C"
    saved = state.load(repo)
    assert saved is not None and saved.task_id == "WL-1"
    assert not (repo / ".whyline" / "relay" / "running.json").exists()


def test_stop_file_during_the_run_cancels_the_agent_and_saves_timeout(repo: Path):
    pid_file = repo / "agent.pid"

    def write_stop_once_running() -> None:
        deadline = time.monotonic() + 15
        while not pid_file.exists():
            if time.monotonic() > deadline:
                return
            time.sleep(0.05)
        loop.stop_path(repo).write_text("")

    # The hang is 600s and timeout 0 disables the watchdog. A missed STOP must
    # not stall the suite; this only fires if cancellation fails.
    guard = threading.Timer(20, os.kill, (os.getpid(), signal.SIGINT))
    guard.daemon = True
    writer = threading.Thread(target=write_stop_once_running)
    previous = signal.getsignal(signal.SIGINT)
    writer.start()
    guard.start()
    try:
        code = cli.main(["start", "--repo", str(repo), "--timeout", "0"])
    finally:
        guard.cancel()
        signal.signal(signal.SIGINT, previous)
        writer.join(timeout=2)
    assert pid_file.exists(), "the agent never started, so this test proved nothing"
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        outlived = False
    else:
        outlived = True
        os.kill(pid, signal.SIGKILL)
    assert not outlived, "the agent outlived the STOP file"
    assert code == cli.EXIT_PAUSED
    saved = state.load(repo)
    assert saved is not None and saved.task_id == "WL-1"
    assert "cancelled by user" in saved.paused_reason
    assert saved.timeout_minutes == 0
    raw = json.loads(state.path(repo).read_text(encoding="utf-8"))
    assert raw["timeout_minutes"] == 0
    assert not (repo / ".whyline" / "relay" / "running.json").exists()


def test_stop_command_reports_that_the_current_agent_stops(repo: Path, capsys):
    assert cli.main(["stop", "--repo", str(repo)]) == cli.EXIT_OK
    assert (
        capsys.readouterr().out.strip()
        == "Stopped the current agent. Nothing new will start."
    )
    assert (
        "Stop the current agent and do not start another."
        in cli.build_parser().format_help()
    )
