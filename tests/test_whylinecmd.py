import subprocess
from pathlib import Path

import pytest

from whyline_relay import whylinecmd


class FakeRun:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_sync_passes_the_task_and_returns_stdout(tmp_path: Path):
    fake = FakeRun(stdout="SYNC PACKET")
    assert whylinecmd.sync(tmp_path, "WL-1", runner=fake) == "SYNC PACKET"
    argv = fake.calls[0][0]
    assert argv[:2] == ["whyline", "sync"]
    assert "--task" in argv and "WL-1" in argv


def test_sync_raises_when_whyline_fails(tmp_path: Path):
    with pytest.raises(whylinecmd.WhylineUnavailable):
        whylinecmd.sync(tmp_path, "WL-1", runner=FakeRun(returncode=2))


def test_claim_sends_actor_and_role(tmp_path: Path):
    fake = FakeRun()
    whylinecmd.claim(tmp_path, "WL-1", "codex", "implementer", runner=fake)
    argv = fake.calls[0][0]
    assert argv[:2] == ["whyline", "claim"]
    assert "--actor" in argv and "codex" in argv
    assert "--role" in argv and "implementer" in argv
