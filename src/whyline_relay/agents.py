"""Launching one agent as a child process, and watching it finish.

We supervise a child; we never exec. The parent must survive to route the next
turn. Output is teed for the human and the log, and is never parsed to decide
anything — routing comes from whyline's handoff record alone.
"""

from __future__ import annotations

import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

class RunResult(NamedTuple):
    exit_code: int
    output: str | None

# After SIGTERM, how long an agent gets to exit before it is sent SIGKILL. Read at call
# time so a test can shorten it.
KILL_GRACE_SECONDS = 5
HEARTBEAT_SECONDS = 30.0
# Event.wait takes one event. The cancel listener slices at 50ms so it notices
# whichever of cancel_event and stop_listener was set.
_CANCEL_POLL_SECONDS = 0.05

RATE_LIMIT_MARKERS = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "quota exceeded",
    "too many requests",
    "hit your limit",
    "limit reached",
    "session limit",
    "weekly limit",
    "out of credits",
    # Prepaid balance or billing exhausted. Grok says "402 Payment Required:
    # Grok Build usage balance exhausted", which none of the above matched.
    "payment required",
    "balance exhausted",
    "credit balance is too low",
    "insufficient credits",
    "insufficient_quota",
    "exceeded your current quota",
)

_terminal_lock = threading.Lock()


class AgentMissing(RuntimeError):
    """The agent's binary is not installed."""


class AgentTimeout(RuntimeError):
    """The agent outlived its timeout and was killed."""


class AgentCancelled(RuntimeError):
    """The user cancelled the agent."""


def _which(name: str) -> str | None:
    """Looked up at call time. Never cache this, and never bind it as a default.

    whyline's runner.py carries the scars: a cached lookup ignores a test's
    patch and execs the real vendor CLI, which hung a test run on 2026-08-18.
    """
    return shutil.which(name)


def build_argv(command: list[str], prompt: str) -> list[str]:
    return [*command, prompt]


def rate_limited(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in RATE_LIMIT_MARKERS)


def format_duration(seconds: float) -> str:
    """Format elapsed time for the relay's compact progress lines."""
    whole_seconds = max(0, int(seconds))
    minutes, remainder = divmod(whole_seconds, 60)
    if minutes:
        return f"{minutes}m{remainder}s"
    return f"{remainder}s"


def print_status(message: str) -> None:
    """Write one timestamped, terminal-only relay status line."""
    with _terminal_lock:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def _heartbeat_interval() -> float:
    raw = os.environ.get("WHYLINE_RELAY_HEARTBEAT_SECONDS")
    if raw is None:
        return HEARTBEAT_SECONDS
    try:
        interval = float(raw)
    except ValueError:
        return HEARTBEAT_SECONDS
    return interval if interval > 0 and math.isfinite(interval) else HEARTBEAT_SECONDS


def _stop_timer(timer: threading.Timer | None) -> None:
    """Cancel a timer and wait it out. A callback already running survives cancel."""
    if timer is None or timer.ident is None:
        return
    timer.cancel()
    timer.join()


def _child_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    """Environment for one child. None inherits; a mapping overrides its keys.

    The returned dict is a copy. The relay's own `os.environ` is not changed.
    """
    if env is None:
        return None
    merged = dict(os.environ)
    merged.update(env)
    return merged


def run(
    command: list[str],
    prompt: str,
    *,
    cwd: Path,
    log_path: Path,
    timeout_seconds: int | None,
    which=None,
    echo: bool = True,
    agent_name: str | None = None,
    capture: bool = False,
    cancel_event: threading.Event | None = None,
    env: Mapping[str, str] | None = None,
) -> RunResult:
    """Run one agent to completion. Returns a RunResult.

    Raises AgentMissing if the binary is absent, AgentTimeout if it overruns,
    and AgentCancelled if the user cancels it. `timeout_seconds` is required:
    a positive int arms the watchdog, and None waits without one. `env` is
    merged over the relay's environment for this child only; None leaves the
    child with today's inherited environment.
    """
    lookup = which if which is not None else _which
    argv = build_argv(command, prompt)
    if lookup(argv[0]) is None:
        raise AgentMissing(f"{argv[0]} is not installed or not on PATH")
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds <= 0
    ):
        raise ValueError(
            "timeout_seconds must be a positive integer or None, "
            f"got {timeout_seconds!r}"
        )
    if cancel_event is not None and cancel_event.is_set():
        raise AgentCancelled(f"{argv[0]} cancelled by user")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    timed_out = threading.Event()
    stop_listener = threading.Event()
    kill_lock = threading.Lock()
    kill_started = False
    hard_kill: threading.Timer | None = None
    terminal_name = agent_name or Path(argv[0]).name
    started = time.monotonic()
    last_output = started
    heartbeat_stopped = False
    heartbeat_condition = threading.Condition()
    buffer: list[str] = [] if capture else None

    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
        env=_child_env(env),
    )

    def signal_group(signum: int) -> None:
        try:
            os.killpg(os.getpgid(process.pid), signum)
        except (ProcessLookupError, PermissionError):
            pass

    def begin_kill() -> None:
        """SIGTERM the group once, and arm one SIGKILL timer.

        The watchdog, the cancel listener, and the BaseException path share
        this. A second caller must not send another SIGTERM or arm another timer.
        """
        nonlocal kill_started, hard_kill
        with kill_lock:
            if kill_started:
                return
            kill_started = True
            # An agent that ignores SIGTERM must not outlive the grace period.
            hard_kill = threading.Timer(
                KILL_GRACE_SECONDS, signal_group, args=(signal.SIGKILL,)
            )
            hard_kill.daemon = True
            signal_group(signal.SIGTERM)
            hard_kill.start()

    def on_timeout() -> None:
        timed_out.set()
        begin_kill()

    # None must not construct a Timer at all, including Timer(0) as a stand-in.
    watchdog: threading.Timer | None = None
    if isinstance(timeout_seconds, int) and not isinstance(timeout_seconds, bool):
        watchdog = threading.Timer(timeout_seconds, on_timeout)
        watchdog.daemon = True

    def listen_for_cancel() -> None:
        assert cancel_event is not None
        while not stop_listener.is_set():
            if cancel_event.wait(_CANCEL_POLL_SECONDS):
                begin_kill()
                return

    listener: threading.Thread | None = None
    if cancel_event is not None:
        listener = threading.Thread(
            target=listen_for_cancel, name="whyline-cancel", daemon=True
        )

    def heartbeat() -> None:
        interval = _heartbeat_interval()
        observed_output = last_output
        next_report = observed_output + interval
        while True:
            with heartbeat_condition:
                if heartbeat_stopped:
                    return
                now = time.monotonic()
                if last_output != observed_output:
                    observed_output = last_output
                    next_report = observed_output + interval
                remaining = next_report - now
                if remaining > 0:
                    heartbeat_condition.wait(timeout=remaining)
                    continue
                next_report += interval
                print_status(
                    f"... {terminal_name} still running "
                    f"({format_duration(time.monotonic() - started)})"
                )

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    try:
        if watchdog is not None:
            watchdog.start()
        if listener is not None:
            listener.start()
        if echo:
            heartbeat_thread.start()
        with log_path.open("w", encoding="utf-8") as log:
            for line in process.stdout or ():
                log.write(line)
                log.flush()
                if capture:
                    buffer.append(line)
                if echo:
                    with heartbeat_condition:
                        last_output = time.monotonic()
                        heartbeat_condition.notify()
                    if not capture:
                        with _terminal_lock:
                            sys.stdout.write(line)
                            sys.stdout.flush()
        code = process.wait()
    except BaseException:
        # Ctrl+C, or anything else that unwinds us: the agent must not outlive
        # the relay. It runs in its own session, so SIGINT never reaches it.
        # KeyboardInterrupt is re-raised as itself, not turned into AgentCancelled.
        begin_kill()
        process.wait()
        raise
    finally:
        stop_listener.set()
        if listener is not None and listener.ident is not None:
            listener.join()
        with heartbeat_condition:
            heartbeat_stopped = True
            heartbeat_condition.notify()
        if heartbeat_thread.ident is not None:
            heartbeat_thread.join()
        _stop_timer(watchdog)
        _stop_timer(hard_kill)
    if cancel_event is not None and cancel_event.is_set():
        raise AgentCancelled(f"{argv[0]} cancelled by user")
    if timed_out.is_set():
        raise AgentTimeout(
            f"{argv[0]} exceeded {timeout_seconds}s and was terminated"
        )
    return RunResult(code, "".join(buffer) if capture else None)


def terminate(process: subprocess.Popen) -> None:
    """Stop a running agent's whole process group. Used by the SIGINT handler."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
