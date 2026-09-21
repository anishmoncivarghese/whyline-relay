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
from datetime import datetime
from pathlib import Path

# After SIGTERM, how long an agent gets to exit before it is sent SIGKILL. Read at call
# time so a test can shorten it.
KILL_GRACE_SECONDS = 5
HEARTBEAT_SECONDS = 30.0

RATE_LIMIT_MARKERS = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "quota exceeded",
    "too many requests",
)

_terminal_lock = threading.Lock()


class AgentMissing(RuntimeError):
    """The agent's binary is not installed."""


class AgentTimeout(RuntimeError):
    """The agent outlived its timeout and was killed."""


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


def run(
    command: list[str],
    prompt: str,
    *,
    cwd: Path,
    log_path: Path,
    timeout_seconds: int,
    which=None,
    echo: bool = True,
    agent_name: str | None = None,
) -> int:
    """Run one agent to completion. Returns its exit code.

    Raises AgentMissing if the binary is absent, AgentTimeout if it overruns.
    """
    lookup = which if which is not None else _which
    argv = build_argv(command, prompt)
    if lookup(argv[0]) is None:
        raise AgentMissing(f"{argv[0]} is not installed or not on PATH")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    timed_out = threading.Event()
    terminal_name = agent_name or Path(argv[0]).name
    started = time.monotonic()
    last_output = started
    heartbeat_stopped = False
    heartbeat_condition = threading.Condition()

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
    )

    def signal_group(signum: int) -> None:
        try:
            os.killpg(os.getpgid(process.pid), signum)
        except (ProcessLookupError, PermissionError):
            pass

    def kill_group() -> None:
        timed_out.set()
        signal_group(signal.SIGTERM)
        # An agent that ignores SIGTERM must not outlive its timeout.
        hard_kill.start()

    hard_kill = threading.Timer(KILL_GRACE_SECONDS, signal_group, args=(signal.SIGKILL,))
    watchdog = threading.Timer(timeout_seconds, kill_group)

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
    watchdog.start()
    if echo:
        heartbeat_thread.start()
    try:
        with log_path.open("w", encoding="utf-8") as log:
            for line in process.stdout or ():
                log.write(line)
                log.flush()
                if echo:
                    with heartbeat_condition:
                        last_output = time.monotonic()
                        heartbeat_condition.notify()
                    with _terminal_lock:
                        sys.stdout.write(line)
                        sys.stdout.flush()
        code = process.wait()
    except BaseException:
        # Ctrl+C, or anything else that unwinds us: the agent must not outlive
        # the relay. It runs in its own session, so SIGINT never reaches it.
        signal_group(signal.SIGTERM)
        try:
            process.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            signal_group(signal.SIGKILL)
            process.wait()
        raise
    finally:
        with heartbeat_condition:
            heartbeat_stopped = True
            heartbeat_condition.notify()
        if echo:
            heartbeat_thread.join()
        watchdog.cancel()
        hard_kill.cancel()
    if timed_out.is_set():
        raise AgentTimeout(
            f"{argv[0]} exceeded {timeout_seconds}s and was terminated"
        )
    return code


def terminate(process: subprocess.Popen) -> None:
    """Stop a running agent's whole process group. Used by the SIGINT handler."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
