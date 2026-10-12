import inspect
import os
import re
import signal
import subprocess
import sys
import threading
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


def test_timeout_stays_required_and_cancel_defaults_to_none():
    signature = inspect.signature(agents.run)
    timeout = signature.parameters["timeout_seconds"]
    cancel = signature.parameters["cancel_event"]
    env = signature.parameters["env"]
    assert timeout.kind is inspect.Parameter.KEYWORD_ONLY
    assert timeout.default is inspect.Parameter.empty
    assert timeout.annotation in {"int | None", int | None}
    assert cancel.kind is inspect.Parameter.KEYWORD_ONLY
    assert cancel.default is None
    assert env.kind is inspect.Parameter.KEYWORD_ONLY
    assert env.default is None
    assert issubclass(agents.AgentCancelled, RuntimeError)


def test_run_streams_output_to_the_log(tmp_path: Path):
    log = tmp_path / "run.log"
    result = agents.run(
        [sys.executable, FAKE, "review", str(tmp_path)],
        "the prompt",
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
    )
    assert result.exit_code == 0
    assert "fake-agent review received" in log.read_text()


def test_run_returns_the_agents_exit_code(tmp_path: Path):
    result = agents.run(
        [sys.executable, FAKE, "fail", str(tmp_path)],
        "p",
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
    )
    assert result.exit_code == 1


def test_run_returns_a_runresult_with_no_output_by_default(tmp_path: Path):
    result = agents.run(
        [sys.executable, FAKE, "review", str(tmp_path)],
        "the prompt",
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
    )
    assert isinstance(result, agents.RunResult)
    assert result.exit_code == 0
    assert result.output is None


def test_run_merges_env_for_the_child_and_leaves_os_environ(tmp_path: Path, monkeypatch):
    """A passed mapping overrides keys in the child only."""
    monkeypatch.setenv("UV_CACHE_DIR", "/parent/cache")
    monkeypatch.setenv("WHYLINE_RELAY_KEEP", "parent")
    before = dict(os.environ)
    cache = tmp_path / "cache" / "uv"
    result = agents.run(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ['UV_CACHE_DIR']); print(os.environ['WHYLINE_RELAY_KEEP'])",
        ],
        "the prompt",
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
        env={"UV_CACHE_DIR": str(cache)},
    )
    assert result.exit_code == 0
    assert (tmp_path / "run.log").read_text().splitlines() == [str(cache), "parent"]
    assert dict(os.environ) == before
    assert os.environ["UV_CACHE_DIR"] == "/parent/cache"


def test_run_without_env_inherits_the_parent_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UV_CACHE_DIR", "/already/set")
    before = dict(os.environ)
    result = agents.run(
        [sys.executable, "-c", "import os; print(os.environ['UV_CACHE_DIR'])"],
        "the prompt",
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
    )
    assert result.exit_code == 0
    assert (tmp_path / "run.log").read_text() == "/already/set\n"
    assert dict(os.environ) == before


def test_run_captures_stdout_when_capture_is_true(tmp_path: Path):
    result = agents.run(
        [sys.executable, FAKE, "review", str(tmp_path)],
        "the prompt",
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
        capture=True,
    )
    assert result.exit_code == 0
    assert "fake-agent review received" in result.output


def test_capture_suppresses_raw_echo_but_not_the_heartbeat(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setenv("WHYLINE_RELAY_HEARTBEAT_SECONDS", "0.05")
    result = agents.run(
        [
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(0.18)",
        ],
        "p",
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=30,
        which=lambda name: name,
        agent_name="claude",
        capture=True,
    )
    output = capsys.readouterr().out
    assert "started" not in output
    heartbeat = r"\[\d{2}:\d{2}:\d{2}\] \.\.\. claude still running \(\d+s\)"
    assert len(re.findall(heartbeat, output)) >= 2
    assert "started\n" in result.output


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
    result = agents.run(
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
    assert result.exit_code == 0
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
    result = agents.run(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe done\\n')"],
        "p",
        cwd=tmp_path,
        log_path=log,
        timeout_seconds=30,
        which=lambda name: name,
        echo=False,
    )
    assert result.exit_code == 0
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


def _refuse_spawn(*args, **kwargs):
    raise AssertionError("agent was spawned")


def _refuse_timer(*args, **kwargs):
    raise AssertionError(f"Timer was constructed: {args!r} {kwargs!r}")


def _immune_to_sigterm(ready: Path) -> list[str]:
    script = (
        "import signal, time, pathlib\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(ready)!r}).write_text('up')\n"
        "print('started', flush=True)\n"
        "time.sleep(30)\n"
    )
    return [sys.executable, "-c", script]


def _cancel_when_ready(ready: Path, cancel: threading.Event) -> None:
    deadline = time.monotonic() + 10
    while not ready.exists():
        if time.monotonic() > deadline:
            return
        time.sleep(0.01)
    cancel.set()


def test_cancel_before_popen_does_not_spawn(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _refuse_spawn)
    monkeypatch.setattr(threading, "Timer", _refuse_timer)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(agents.AgentCancelled, match=r"cancelled by user$"):
        agents.run(
            [sys.executable, FAKE, "review", str(tmp_path)],
            "the prompt",
            cwd=tmp_path,
            log_path=tmp_path / "run.log",
            timeout_seconds=30,
            which=lambda name: name,
            echo=False,
            cancel_event=cancel,
        )
    assert not (tmp_path / ".whyline" / "active-handoff.json").exists()


def test_cancel_during_readline_kills_a_child_that_ignores_sigterm(
    tmp_path: Path, monkeypatch
):
    """Cancel unblocks readline: SIGTERM the group, SIGKILL after the grace, reap."""
    monkeypatch.setattr(agents, "KILL_GRACE_SECONDS", 1)
    kills: list[tuple[float, int, int]] = []
    real_killpg = os.killpg

    def spy_killpg(pgid: int, sig: int) -> None:
        kills.append((time.monotonic(), pgid, sig))
        real_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", spy_killpg)
    processes: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def tracking_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", tracking_popen)
    ready = tmp_path / "ready"
    cancel = threading.Event()
    helper = threading.Thread(target=_cancel_when_ready, args=(ready, cancel), daemon=True)
    helper.start()
    started = time.monotonic()
    try:
        with pytest.raises(agents.AgentCancelled, match=r"cancelled by user$"):
            agents.run(
                _immune_to_sigterm(ready),
                "p",
                cwd=tmp_path,
                log_path=tmp_path / "run.log",
                timeout_seconds=30,
                which=lambda name: name,
                echo=False,
                cancel_event=cancel,
            )
    finally:
        helper.join(timeout=2)
    assert time.monotonic() - started < 6
    assert [sig for _, _, sig in kills] == [signal.SIGTERM, signal.SIGKILL]
    assert kills[0][1] == processes[0].pid
    # The gap is the grace period, not an immediate SIGKILL.
    assert kills[1][0] - kills[0][0] >= 0.7
    assert processes[0].returncode is not None


def test_timeout_and_cancel_share_one_kill(tmp_path: Path, monkeypatch):
    """Both firing in one grace period still send one SIGTERM and arm one SIGKILL."""
    monkeypatch.setattr(agents, "KILL_GRACE_SECONDS", 2)
    kills: list[int] = []
    real_killpg = os.killpg

    def spy_killpg(pgid: int, sig: int) -> None:
        kills.append(sig)
        real_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", spy_killpg)
    created: list[tuple] = []
    real_timer = threading.Timer

    def spy_timer(interval, function, args=None, kwargs=None):
        created.append((interval, args))
        return real_timer(interval, function, args=args, kwargs=kwargs)

    monkeypatch.setattr(threading, "Timer", spy_timer)
    ready = tmp_path / "ready"
    cancel = threading.Event()
    before = set(threading.enumerate())
    helper = threading.Thread(
        target=_cancel_when_ready, args=(ready, cancel), daemon=True
    )
    helper.start()
    started = time.monotonic()
    try:
        with pytest.raises(agents.AgentCancelled, match=r"cancelled by user$"):
            agents.run(
                _immune_to_sigterm(ready),
                "p",
                cwd=tmp_path,
                log_path=tmp_path / "run.log",
                timeout_seconds=1,
                which=lambda name: name,
                echo=False,
                cancel_event=cancel,
            )
    finally:
        helper.join(timeout=2)
    assert time.monotonic() - started < 8
    assert kills.count(signal.SIGTERM) == 1
    assert kills.count(signal.SIGKILL) == 1
    sigkill_timers = [item for item in created if item[1] == (signal.SIGKILL,)]
    assert len(sigkill_timers) == 1
    assert sigkill_timers[0][0] == 2
    leftover = [
        thread
        for thread in threading.enumerate()
        if thread not in before and thread.is_alive() and thread is not helper
    ]
    assert leftover == []


def test_normal_exit_racing_cancel_is_a_single_outcome(tmp_path: Path, monkeypatch):
    """A finish and a cancel may overlap. One call does only one of these:

    return RunResult and send no signal, or raise AgentCancelled after reap.
    """
    signals: list[int] = []
    real_killpg = os.killpg

    def spy_killpg(pgid: int, sig: int) -> None:
        signals.append(sig)
        real_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", spy_killpg)
    processes: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def tracking_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", tracking_popen)

    def once(delay: float, cancel_after: float) -> str:
        signals.clear()
        ready = tmp_path / "ready"
        ready.unlink(missing_ok=True)
        cancel = threading.Event()
        script = (
            "import time, sys, pathlib\n"
            f"pathlib.Path({str(ready)!r}).write_text('up')\n"
            "time.sleep(float(sys.argv[1]))\n"
            "print('done', flush=True)\n"
        )

        def trip() -> None:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if time.monotonic() > deadline:
                    return
                time.sleep(0.005)
            time.sleep(cancel_after)
            cancel.set()

        helper = threading.Thread(target=trip, daemon=True)
        before = len(processes)
        helper.start()
        try:
            result = agents.run(
                [sys.executable, "-c", script, str(delay)],
                "p",
                cwd=tmp_path,
                log_path=tmp_path / "run.log",
                timeout_seconds=30,
                which=lambda name: name,
                echo=False,
                cancel_event=cancel,
            )
        except agents.AgentCancelled as exc:
            outcome = "cancelled"
            assert str(exc).endswith("cancelled by user")
        else:
            outcome = "finished"
            assert isinstance(result, agents.RunResult)
            assert result.exit_code == 0
            assert signals == []
        finally:
            helper.join(timeout=5)
        assert len(processes) == before + 1
        assert processes[-1].returncode is not None
        return outcome

    assert once(0.0, 2.0) == "finished"
    assert once(2.0, 0.05) == "cancelled"
    for delay in (0.0, 0.03, 0.06):
        assert once(delay, 0.04) in {"finished", "cancelled"}


def test_no_timeout_starts_no_timer_and_still_heartbeats(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setattr(threading, "Timer", _refuse_timer)
    monkeypatch.setenv("WHYLINE_RELAY_HEARTBEAT_SECONDS", "0.05")
    result = agents.run(
        [
            sys.executable,
            "-c",
            "import time; print('started', flush=True); time.sleep(0.18)",
        ],
        "p",
        cwd=tmp_path,
        log_path=tmp_path / "run.log",
        timeout_seconds=None,
        which=lambda name: name,
        agent_name="claude",
    )
    output = capsys.readouterr().out
    assert isinstance(result, agents.RunResult)
    assert result.exit_code == 0
    assert "started\n" in output
    heartbeat = r"\[\d{2}:\d{2}:\d{2}\] \.\.\. claude still running \(\d+s\)"
    assert len(re.findall(heartbeat, output)) >= 2
    time.sleep(0.1)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("timeout_seconds", [0, -1, True, False])
def test_non_positive_timeout_does_not_spawn(
    tmp_path: Path, monkeypatch, timeout_seconds
):
    monkeypatch.setattr(subprocess, "Popen", _refuse_spawn)
    monkeypatch.setattr(threading, "Timer", _refuse_timer)
    with pytest.raises(ValueError, match="timeout_seconds"):
        agents.run(
            [sys.executable, FAKE, "review", str(tmp_path)],
            "p",
            cwd=tmp_path,
            log_path=tmp_path / "run.log",
            timeout_seconds=timeout_seconds,
            which=lambda name: name,
            echo=False,
        )


def test_keyboard_interrupt_is_not_agent_cancelled(tmp_path: Path, monkeypatch):
    real_popen = subprocess.Popen

    def tracking_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        original_wait = process.wait

        def wait(timeout=None):
            process.wait = original_wait
            raise KeyboardInterrupt

        process.wait = wait
        return process

    monkeypatch.setattr(subprocess, "Popen", tracking_popen)
    with pytest.raises(KeyboardInterrupt):
        agents.run(
            [sys.executable, "-c", "print('hi', flush=True)"],
            "p",
            cwd=tmp_path,
            log_path=tmp_path / "run.log",
            timeout_seconds=30,
            which=lambda name: name,
            echo=False,
        )
