"""pid_is_alive must never signal a process on Windows: os.kill(pid, 0) there
is CTRL_C_EVENT, which interrupts every process on that console, including
the caller when it checks its own pid."""

import os

from whyline_relay import running


def test_windows_asks_the_os_and_never_calls_os_kill(monkeypatch):
    kills = []
    asked = []
    monkeypatch.setattr(running.os, "name", "nt")
    monkeypatch.setattr(running.os, "kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr(running, "_windows_pid_alive", lambda pid: asked.append(pid) or True)
    assert running.pid_is_alive(4242) is True
    assert asked == [4242]
    assert kills == []


def test_windows_dead_pid_is_not_alive(monkeypatch):
    monkeypatch.setattr(running.os, "name", "nt")
    monkeypatch.setattr(running.os, "kill", lambda pid, sig: (_ for _ in ()).throw(AssertionError("signalled")))
    monkeypatch.setattr(running, "_windows_pid_alive", lambda pid: False)
    assert running.pid_is_alive(4242) is False


def test_non_positive_pids_are_never_alive(monkeypatch):
    monkeypatch.setattr(running, "_windows_pid_alive", lambda pid: (_ for _ in ()).throw(AssertionError("asked")))
    for name in ("nt", "posix"):
        monkeypatch.setattr(running.os, "name", name)
        assert running.pid_is_alive(0) is False
        assert running.pid_is_alive(-1) is False


def test_this_process_is_alive_on_the_real_platform():
    assert running.pid_is_alive(os.getpid()) is True
