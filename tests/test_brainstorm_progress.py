import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from whyline_relay import agents, brainstorm, chat, chatlog, config


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def _ok(agent: str, response: str = "ok") -> dict:
    return {"agent": agent, "response": response, "rate_limited": False, "ok": True}


def test_progress_statuses_name_the_lifecycle():
    assert brainstorm.PROGRESS_STATUSES == (
        "starting",
        "running",
        "succeeded",
        "failed",
        "skipped",
    )


def test_callback_order_across_pass_zero_review_and_synthesis(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    events: list[brainstorm.ProgressEvent] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "independently" in prompt and agent == "codex":
            raise agents.AgentTimeout("codex exceeded 300s")
        if "Combined review pass" in prompt and agent == "claude":
            return {
                "agent": agent,
                "response": "nope",
                "rate_limited": False,
                "ok": False,
            }
        return _ok(agent, "final" if "Final Synthesis" in prompt else "ok")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path, models, "topic", settings=settings,
        print_fn=lambda *a, **k: None, progress_fn=events.append,
    )
    reviewed = brainstorm.run_review_pass(
        tmp_path, models, "topic", 1, settings=settings,
        print_fn=lambda *a, **k: None, progress_fn=events.append,
        actual_agents=actual,
    )
    brainstorm.run_final_synthesis(
        tmp_path, "codex", models, "topic", settings=settings,
        print_fn=lambda *a, **k: None, progress_fn=events.append,
    )

    assert [(event.status, event.agent, event.phase, event.ordinal, event.pass_number) for event in events] == [
        ("starting", "claude", "pass-zero", 1, None),
        ("running", "claude", "pass-zero", 1, None),
        ("succeeded", "claude", "pass-zero", 1, None),
        ("starting", "codex", "pass-zero", 2, None),
        ("running", "codex", "pass-zero", 2, None),
        ("skipped", "codex", "pass-zero", 2, None),
        ("starting", "claude", "review", 1, 1),
        ("running", "claude", "review", 1, 1),
        ("failed", "claude", "review", 1, 1),
        ("starting", "codex", "review", 2, 1),
        ("running", "codex", "review", 2, 1),
        ("succeeded", "codex", "review", 2, 1),
        ("starting", "codex", "synthesis", 1, None),
        ("running", "codex", "synthesis", 1, None),
        ("succeeded", "codex", "synthesis", 1, None),
    ]
    assert events[5].reason == "codex exceeded 300s"
    assert events[8].reason == brainstorm.TURN_FAILURE_REASON
    # Reporting a skip or a failure does not drop the agent from the maps.
    assert actual == {"claude": "claude", "codex": "codex"}
    assert reviewed["claude"] == "claude"
    assert reviewed["codex"] == "codex"
    assert all(event.total == 2 for event in events if event.phase != "synthesis")
    assert events[-1].total == 1


def test_start_and_finish_lines_include_ordinal_label_and_elapsed(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    now = {"t": 50.0}

    def clock() -> float:
        return now["t"]

    monkeypatch.setattr(brainstorm.time, "monotonic", clock)

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if agent == "claude":
            now["t"] = 50.0 + 125.9
            return _ok(agent)
        now["t"] = 50.0 + 125.9 + 4.2
        raise agents.AgentMissing("codex is not installed")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    printed: list[str] = []
    brainstorm.run_pass_zero(
        tmp_path, models, "topic", settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    assert printed == [
        "[1/2] Claude starting pass-zero (0s)",
        "[1/2] Claude succeeded pass-zero (2m5s)",
        "[2/2] Codex starting pass-zero (0s)",
        "[2/2] Codex skipped pass-zero (4s): codex is not installed",
        "Codex could not research this pass: codex is not installed",
    ]


@pytest.mark.parametrize(
    ("seconds", "formatted"),
    [(0, "0s"), (59.9, "59s"), (60, "1m0s"), (345.9, "5m45s")],
)
def test_progress_line_formats_elapsed_time_like_the_relay(seconds: float, formatted: str):
    event = brainstorm.ProgressEvent(
        status="succeeded",
        agent="claude",
        label="Claude",
        phase=brainstorm.PHASE_REVIEW,
        ordinal=2,
        total=4,
        elapsed_seconds=seconds,
        pass_number=3,
    )
    assert brainstorm.format_progress_line(event) == (
        f"[2/4] Claude succeeded review pass 3 ({formatted})"
    )
    assert agents.format_duration(seconds) == formatted


def test_running_is_reported_to_the_callback_and_not_printed(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    events: list[brainstorm.ProgressEvent] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        assert [event.status for event in events] == ["starting", "running"]
        return _ok(agent, "final answer")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    printed: list[str] = []
    brainstorm.run_final_synthesis(
        tmp_path, "claude", [("claude", "Claude")], "topic", settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        progress_fn=events.append,
    )
    assert [event.status for event in events] == ["starting", "running", "succeeded"]
    assert printed == [
        "[1/1] Claude starting final synthesis (0s)",
        "[1/1] Claude succeeded final synthesis (0s)",
    ]


def test_skipped_final_synthesis_reports_then_reraises(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    events: list[brainstorm.ProgressEvent] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        raise chat.AgentUnavailable("claude is not configured")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    printed: list[str] = []
    with pytest.raises(chat.AgentUnavailable, match="not configured"):
        brainstorm.run_final_synthesis(
            tmp_path, "claude", [("claude", "Claude")], "topic",
            settings=settings,
            print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
            progress_fn=events.append,
        )
    assert [event.status for event in events] == ["starting", "running", "skipped"]
    assert printed == [
        "[1/1] Claude starting final synthesis (0s)",
        "[1/1] Claude skipped final synthesis (0s): claude is not configured",
    ]


def test_progress_event_payload_is_json_serializable():
    event = brainstorm.ProgressEvent(
        status="failed",
        agent="grok",
        label="Grok",
        phase=brainstorm.PHASE_PASS_ZERO,
        ordinal=1,
        total=1,
        elapsed_seconds=1.5,
        pass_number=None,
        reason=brainstorm.TURN_FAILURE_REASON,
    )
    payload = json.loads(json.dumps(asdict(event)))
    assert payload == {
        "status": "failed",
        "agent": "grok",
        "label": "Grok",
        "phase": "pass-zero",
        "ordinal": 1,
        "total": 1,
        "elapsed_seconds": 1.5,
        "pass_number": None,
        "reason": "turn reported a failure",
    }


def test_progress_lines_stay_out_of_the_agent_log_and_chat_history(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    seen: dict = {}

    def fake_run_fn(command, prompt, **kwargs):
        seen["capture"] = kwargs.get("capture")
        seen["echo"] = kwargs.get("echo")
        log_path = kwargs["log_path"]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("agent-stdout-marker\n", encoding="utf-8")
        seen["log_path"] = log_path
        return agents.RunResult(0, '{"type":"result","result":"synthesized"}\n')

    printed: list[str] = []
    record = brainstorm.run_final_synthesis(
        tmp_path, "claude", [("claude", "Claude")], "topic",
        settings=settings, run_fn=fake_run_fn,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    log_text = seen["log_path"].read_text(encoding="utf-8")
    history = chatlog.load(tmp_path)
    assert seen["capture"] is True
    assert seen["echo"] is True
    assert seen["log_path"] == (
        tmp_path / ".whyline" / "relay" / "logs" / "chat-last-turn.log"
    )
    assert log_text == "agent-stdout-marker\n"
    assert "starting" not in log_text
    assert record["response"] == "synthesized"
    assert history[-1]["response"] == "synthesized"
    assert "starting" not in history[-1]["prompt"]
    assert "[1/1]" not in json.dumps(history)
    assert printed[0] == "[1/1] Claude starting final synthesis (0s)"
    assert printed[-1] == "[1/1] Claude succeeded final synthesis (0s)"


def test_repl_prints_start_and_finish_lines_for_each_phase(tmp_path: Path):
    _init_repo(tmp_path)
    chat.save_default_agent(tmp_path, "claude")
    answers = iter([
        "/brainstorm",
        "caching strategy",
        "1",  # Claude only
        "1",  # one review pass
        "",  # final synthesis defaults to claude
        "/exit",
    ])

    def fake_run_fn(command, prompt, **kwargs):
        return agents.RunResult(0, '{"type":"result","result":"an answer"}\n')

    printed: list[str] = []
    chat.repl(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert "[1/1] Claude starting pass-zero (0s)" in printed
    assert "[1/1] Claude succeeded pass-zero (0s)" in printed
    assert "[1/1] Claude starting review pass 1 (0s)" in printed
    assert "[1/1] Claude succeeded review pass 1 (0s)" in printed
    assert "[1/1] Claude starting final synthesis (0s)" in printed
    assert "[1/1] Claude succeeded final synthesis (0s)" in printed
    assert any(line == "[claude] an answer" for line in printed)
