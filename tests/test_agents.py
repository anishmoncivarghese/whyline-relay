import inspect
import sys
import time
from pathlib import Path

import pytest

from whyline_relay import agents

FAKE = str(Path(__file__).parent / "fake_agent.py")


def test_build_argv_appends_the_prompt_last():
    argv = agents.build_argv(["codex", "exec", "-s", "workspace-write"], "do it")
    assert argv == ["codex", "exec", "-s", "workspace-write", "do it"]


def test_which_is_not_bound_as_a_default_argument():
    """Binding it would ignore a later patch and exec the real agent. Twice bitten."""
    signature = inspect.signature(agents.run)
    assert signature.parameters["which"].default is None


def test_run_streams_output_to_the_log(tmp_path: Path):
    log = tmp_path / "run.log"
    code = agents.run(
        [sys.executable, FAKE, "review", str(tmp_path)],
        "the prompt",
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
    )
    assert code == 0
    assert "fake-agent review received" in log.read_text()


def test_run_returns_the_agents_exit_code(tmp_path: Path):
    code = agents.run(
        [sys.executable, FAKE, "fail", str(tmp_path)],
        "p",
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
    )
    assert code == 1


def test_missing_binary_raises_before_launching(tmp_path: Path):
    with pytest.raises(agents.AgentMissing):
        agents.run(
            ["definitely-not-installed"],
            "p",
            cwd=tmp_path,
            log_path=tmp_path / "run.log",
            timeout_seconds=30,
            which=lambda name: None,
            echo=False,
        )


def test_timeout_kills_the_agent_and_raises(tmp_path: Path):
    log = tmp_path / "run.log"
    with pytest.raises(agents.AgentTimeout):
        agents.run(
            [sys.executable, FAKE, "hang", str(tmp_path)],
            "p",
            cwd=tmp_path,
            log_path=log,
            timeout_seconds=1,
            which=lambda name: name,
            echo=False,
        )


def test_rate_limit_marker_is_recognised():
    assert agents.rate_limited("You have exceeded your usage limit. Try again later.")
    assert not agents.rate_limited("all tests passed")


def test_non_utf8_output_does_not_crash_the_relay(tmp_path: Path):
    """An agent can print bytes that are not valid UTF-8. That must not kill the relay."""
    log = tmp_path / "run.log"
    code = agents.run(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe done\\n')"],
        "p",
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
    )
    assert code == 0
    assert "ok" in log.read_text() and "done" in log.read_text()


def test_timeout_escalates_to_sigkill_for_an_agent_that_ignores_sigterm(
    tmp_path: Path, monkeypatch
):
    """A hung agent that ignores SIGTERM must still be stopped, or the timeout means nothing."""
    monkeypatch.setattr(agents, "KILL_GRACE_SECONDS", 1)
    stubborn = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('started', flush=True); time.sleep(8)"
    )
    started = time.monotonic()
    with pytest.raises(agents.AgentTimeout):
        agents.run(
            [sys.executable, "-c", stubborn],
            "p",
            cwd=tmp_path,
            log_path=tmp_path / "run.log",
            timeout_seconds=1,
            which=lambda name: name,
            echo=False,
        )
    assert time.monotonic() - started < 6
