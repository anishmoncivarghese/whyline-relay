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


def test_note_records_the_decision(tmp_path: Path):
    fake = FakeRun()
    whylinecmd.note(
        tmp_path,
        "Retrying stage 'draft' once more.",
        because="one more visit",
        task="WL-1",
        actor="relay",
        role="resume",
        runner=fake,
    )
    argv, kwargs = fake.calls[0]
    assert argv == [
        "whyline",
        "note",
        "Retrying stage 'draft' once more.",
        "--because",
        "one more visit",
        "--actor",
        "relay",
        "--role",
        "resume",
        "--task",
        "WL-1",
    ]
    assert kwargs["cwd"] == str(tmp_path)


def test_note_raises_when_whyline_fails(tmp_path: Path):
    with pytest.raises(whylinecmd.WhylineUnavailable):
        whylinecmd.note(
            tmp_path,
            "Retrying stage 'draft' once more.",
            because="one more visit",
            task="WL-1",
            actor="relay",
            role="resume",
            runner=FakeRun(returncode=2),
        )


def test_supports_issues_reads_handoff_help_once_per_process(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(whylinecmd, "_issue_support", None)
    fake = FakeRun(stdout="usage: whyline handoff [--risk RISKS] [--issue TEXT]")
    assert whylinecmd.supports_issues(tmp_path, runner=fake) is True
    assert whylinecmd.supports_issues(tmp_path, runner=fake) is True
    assert [argv for argv, _ in fake.calls] == [["whyline", "handoff", "--help"]]


def test_an_older_whyline_does_not_support_issues(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(whylinecmd, "_issue_support", None)
    fake = FakeRun(stdout="usage: whyline handoff [--risk RISKS] [--question QUESTIONS]")
    assert whylinecmd.supports_issues(tmp_path, runner=fake) is False


def test_a_failing_or_missing_whyline_does_not_support_issues(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(whylinecmd, "_issue_support", None)
    assert whylinecmd.supports_issues(tmp_path, runner=FakeRun("--issue", 2)) is False

    def missing(argv, **kwargs):
        raise FileNotFoundError("whyline")

    monkeypatch.setattr(whylinecmd, "_issue_support", None)
    assert whylinecmd.supports_issues(tmp_path, runner=missing) is False
