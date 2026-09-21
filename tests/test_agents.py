import inspect
import re
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


@pytest.mark.parametrize(
    ("seconds", "formatted"),
    [(0, "0s"), (59.9, "59s"), (60, "1m0s"), (345.9, "5m45s")],
)
def test_duration_format(seconds: float, formatted: str):
    assert agents.format_duration(seconds) == formatted


def test_heartbeat_reports_silence_until_the_agent_ends(
    tmp_path: Path, monkeypatch, capsys
):
    log = tmp_path / "run.log"
    monkeypatch.setenv("WHYLINE_RELAY_HEARTBEAT_SECONDS", "0.05")
    code = agents.run(
        [
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(0.18)",
        ],
        "p",
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=30,
        which=lambda name: name,
        agent_name="claude",
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "started\n" in output
    heartbeat = r"\[\d{2}:\d{2}:\d{2}\] \.\.\. claude still running \(\d+s\)"
    assert len(re.findall(heartbeat, output)) >= 2
    assert log.read_text() == "started\n"
    time.sleep(0.1)
    assert capsys.readouterr().out == ""


def test_echo_false_suppresses_heartbeat(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("WHYLINE_RELAY_HEARTBEAT_SECONDS", "0.01")
    agents.run(
        [sys.executable, "-c", "import time; time.sleep(0.04)"],
        "p",
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
        agent_name="codex",
    )
    assert capsys.readouterr().out == ""


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
