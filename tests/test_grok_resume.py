import json
import subprocess
import threading
from pathlib import Path

import pytest

from whyline_relay import chat, chatlog, config
from whyline_relay.adapters import generic, grok
from whyline_relay.agents import AgentCancelled, RunResult

CANCELLED = '{\n  "text": "x",\n  "stopReason": "cancelled",\n  "sessionId": "s-1"\n}\n'
FINISHED = '{\n  "text": "x",\n  "stopReason": "end_turn",\n  "sessionId": "s-1"\n}\n'
COMMAND = ["grok", "--output-format", "json", "--allow", "Edit",
           "--allow", "Bash(git add:*)", "-p"]


def test_a_prefixed_pretty_printed_cancellation_names_its_session():
    output = (
        "permission required: Execute `python -c \"import whyline_relay\"` "
        "was not run, as a headless run cannot ask for approval\n"
        "{\n"
        '  "text": "I will inspect the relay package.",\n'
        '  "stopReason": "cancelled",\n'
        '  "sessionId": "01a16d84-8d8e-7181-a955-e60435e7a788"\n'
        "}\n"
    )

    assert grok.resumable_session(generic.ADAPTER, output, COMMAND) == (
        "01a16d84-8d8e-7181-a955-e60435e7a788"
    )


def test_a_cancelled_grok_turn_names_its_session_for_resuming():
    assert grok.resumable_session(generic.ADAPTER, CANCELLED, COMMAND) == "s-1"


def test_a_finished_or_non_grok_turn_is_not_resumed():
    assert grok.resumable_session(generic.ADAPTER, FINISHED, COMMAND) is None
    assert grok.resumable_session(generic.ADAPTER, CANCELLED, ["agy", "-p"]) is None
    no_session = CANCELLED.replace('"sessionId": "s-1"', '"other": 1')
    assert grok.resumable_session(generic.ADAPTER, no_session, COMMAND) is None


def test_resume_command_keeps_the_prompt_flag_last():
    assert grok.resume_command(COMMAND, "s-1") == [
        *COMMAND[:-1], "--resume", "s-1", "-p",
    ]


def test_a_command_not_ending_in_the_prompt_flag_cannot_be_resumed():
    assert grok.resume_command(["grok", "--prompt-file"], "s-1") is None
    assert grok.resumable_session(
        generic.ADAPTER, CANCELLED, ["grok", "--prompt-file"]
    ) is None


def test_resume_prompt_lists_the_allowed_rules():
    prompt = grok.resume_prompt(COMMAND)
    assert "was stopped" in prompt
    assert "Edit" in prompt and "Bash(git add:*)" in prompt
    assert "hand off" in prompt


def _grok_repo(root: Path) -> "config.Config":
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    relay = root / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        '[agents.grok]\nadapter = "generic"\n'
        'command = ["grok", "--allow", "WebFetch", "-p"]\n'
    )
    return config.load(root)


def _cancelled_payload() -> str:
    return json.dumps(
        {"text": "x", "stopReason": "cancelled", "sessionId": "s-1"}, indent=2
    )


@pytest.mark.parametrize("limit", [900, 1800, 2700, 3600])
def test_each_grok_resume_receives_the_full_timeout(tmp_path: Path, limit: int):
    settings = _grok_repo(tmp_path)
    cancel = threading.Event()
    seen = []

    def fake_run_fn(command, prompt, **kwargs):
        seen.append(kwargs["timeout_seconds"])
        assert kwargs["cancel_event"] is cancel
        return RunResult(0, _cancelled_payload())

    record = chat.run_turn(
        tmp_path, agent="grok", prompt="research", settings=settings,
        run_fn=fake_run_fn, timeout_seconds=limit, cancel_event=cancel,
    )
    assert record["ok"] is False
    assert seen == [limit] * (1 + chat.GROK_RESUMES)
    assert len(seen) == 3


def test_no_limit_reaches_each_grok_resume_as_none(tmp_path: Path):
    settings = _grok_repo(tmp_path)
    seen = []

    def fake_run_fn(command, prompt, **kwargs):
        seen.append(kwargs["timeout_seconds"])
        return RunResult(0, _cancelled_payload())

    chat.run_turn(
        tmp_path, agent="grok", prompt="research", settings=settings,
        run_fn=fake_run_fn, timeout_seconds=chat.NO_LIMIT,
    )
    assert chat.NO_LIMIT is not None
    assert seen == [None] * (1 + chat.GROK_RESUMES)


def test_a_set_event_at_the_start_of_the_iteration_does_not_run(tmp_path: Path):
    settings = _grok_repo(tmp_path)
    cancel = threading.Event()
    cancel.set()
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        calls.append(command)
        return RunResult(0, _cancelled_payload())

    with pytest.raises(AgentCancelled, match="cancelled by user"):
        chat.run_turn(
            tmp_path, agent="grok", prompt="research", settings=settings,
            run_fn=fake_run_fn, cancel_event=cancel,
        )
    assert calls == []
    assert chatlog.load(tmp_path) == []
    subjects = subprocess.run(
        ["git", "log", "--format=%s"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    ).stdout
    assert subjects.strip() == "init"


def test_exit_0_stopreason_cancelled_with_a_set_event_does_not_resume(
    tmp_path: Path, monkeypatch
):
    settings = _grok_repo(tmp_path)
    cancel = threading.Event()
    calls = []
    resume_calls = []
    real = grok.resume_command

    def spy(command, session_id):
        # resumable_session probes with session id "x". A real resume uses s-1.
        if session_id != "x":
            resume_calls.append(session_id)
        return real(command, session_id)

    monkeypatch.setattr(grok, "resume_command", spy)

    def fake_run_fn(command, prompt, **kwargs):
        calls.append(list(command))
        (tmp_path / "partial.md").write_text("half\n")
        cancel.set()
        return RunResult(0, _cancelled_payload())

    with pytest.raises(AgentCancelled, match="cancelled by user"):
        chat.run_turn(
            tmp_path, agent="grok", prompt="research", settings=settings,
            run_fn=fake_run_fn, cancel_event=cancel, timeout_seconds=900,
        )
    assert len(calls) == 1
    assert "--resume" not in calls[0]
    assert resume_calls == []
    assert chatlog.load(tmp_path) == []
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    ).stdout
    assert "partial.md" in status
