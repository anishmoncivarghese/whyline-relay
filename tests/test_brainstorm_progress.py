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
    calls: list[tuple[str, str]] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        calls.append((agent, prompt))
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
        ("failed", "codex", "pass-zero", 2, None),
        ("starting", "claude", "review", 1, 1),
        ("running", "claude", "review", 1, 1),
        ("failed", "claude", "review", 1, 1),
        ("starting", "codex", "synthesis", 1, None),
        ("running", "codex", "synthesis", 1, None),
        ("succeeded", "codex", "synthesis", 1, None),
    ]
    assert events[5].reason == brainstorm.FAILURE_TIMEOUT
    assert events[8].reason == brainstorm.FAILURE_GENERIC
    # Pass-zero excludes unsuccessful research from its returned map.
    assert actual == {"claude": "claude"}
    assert reviewed == {"claude": "claude"}
    assert "codex" not in reviewed

    # Integration assertions: failed pass-zero agent receives no review call,
    # no review lifecycle events, and remains absent from the returned map.
    review_calls = [agent for agent, prompt in calls if "Combined review pass" in prompt]
    assert review_calls == ["claude"]
    assert "codex" not in review_calls

    review_events = [e for e in events if e.phase == brainstorm.PHASE_REVIEW]
    assert [e.agent for e in review_events] == ["claude", "claude", "claude"]
    assert not any(e.agent == "codex" for e in review_events)

    assert [e.total for e in events if e.phase == brainstorm.PHASE_PASS_ZERO] == [2, 2, 2, 2, 2, 2]
    assert [e.total for e in events if e.phase == brainstorm.PHASE_REVIEW] == [1, 1, 1]
    assert [e.total for e in events if e.phase == brainstorm.PHASE_SYNTHESIS] == [1, 1, 1]


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
        "[2/2] Codex skipped pass-zero (4s): missing executable",
        "Codex could not research this pass: missing executable: codex is not installed",
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


def test_classify_failure_categories():
    assert brainstorm.classify_failure(
        record={"ok": False, "rate_limited": True, "response": "out of quota"}
    ) == brainstorm.FAILURE_RATE_LIMIT
    assert brainstorm.classify_failure(
        record={"ok": False, "rate_limited": False, "response": "You have exceeded your usage limit. Try again later."}
    ) == brainstorm.FAILURE_RATE_LIMIT
    assert brainstorm.classify_failure(
        error=agents.AgentTimeout("codex exceeded 300s and was terminated")
    ) == brainstorm.FAILURE_TIMEOUT
    assert brainstorm.classify_failure(
        record={"ok": False, "rate_limited": False, "response": "turn timed out after 300s"}
    ) == brainstorm.FAILURE_TIMEOUT
    assert brainstorm.classify_failure(
        error=agents.AgentMissing("codex is not installed or not on PATH")
    ) == brainstorm.FAILURE_MISSING
    assert brainstorm.classify_failure(
        error=chat.AgentUnavailable("grok is not configured for chat in this repo")
    ) == brainstorm.FAILURE_MISSING
    assert brainstorm.classify_failure(
        error=PermissionError("Permission denied: /dev/null")
    ) == brainstorm.FAILURE_PERMISSION
    assert brainstorm.classify_failure(
        record={"ok": False, "rate_limited": False, "response": "denied permission to run: git commit"}
    ) == brainstorm.FAILURE_PERMISSION
    assert brainstorm.classify_failure(
        record={"ok": False, "rate_limited": False, "failover_notice": "claude is no longer logged in; try again"}
    ) == brainstorm.FAILURE_AUTH
    assert brainstorm.classify_failure(
        record={"ok": False, "rate_limited": False, "response": "Authentication failed: login required"}
    ) == brainstorm.FAILURE_AUTH
    assert brainstorm.classify_failure(
        record={"ok": False, "rate_limited": False, "response": "process crashed unexpectedly"}
    ) == brainstorm.FAILURE_GENERIC


def test_claude_quota_response_is_classified_printed_and_omitted(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    events: list[brainstorm.ProgressEvent] = []
    printed: list[str] = []
    status_map: dict[str, str] = {}

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if agent == "claude":
            return {
                "agent": "claude",
                "response": "You have exceeded your usage limit. Try again later.",
                "rate_limited": True,
                "ok": False,
            }
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text("codex findings\n", encoding="utf-8")
        return _ok(agent, "codex findings")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path,
        models,
        "caching",
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        progress_fn=events.append,
        status_map=status_map,
    )

    # 1. Claude failed and Codex succeeded
    assert actual == {"codex": "codex"}
    assert "claude" not in actual

    # 2. Status map records Claude as failed with quota/rate-limit and Codex as succeeded
    assert status_map["claude"].status == "failed"
    assert status_map["claude"].reason == brainstorm.FAILURE_RATE_LIMIT
    assert status_map["codex"].status == "succeeded"
    assert status_map["codex"].reason is None

    # Also check persisted status map
    persisted_status = brainstorm.load_status_map(tmp_path, "caching")
    assert persisted_status["claude"].status == "failed"
    assert persisted_status["claude"].reason == brainstorm.FAILURE_RATE_LIMIT
    assert persisted_status["codex"].status == "succeeded"
    assert persisted_status["codex"].reason is None
    assert persisted_status == status_map

    # 3. Progress event for Claude reports failed with quota/rate-limit
    claude_terminal = [e for e in events if e.agent == "claude" and e.status in ("failed", "skipped")][0]
    assert claude_terminal.status == "failed"
    assert claude_terminal.reason == brainstorm.FAILURE_RATE_LIMIT

    # 4. Printed output includes start/finish lines and pass-zero explanation
    assert "[1/2] Claude failed pass-zero (0s): quota/rate-limit" in printed
    assert "Claude could not research this pass: quota/rate-limit" in printed
    assert "[2/2] Codex succeeded pass-zero (0s)" in printed

    # 5. Merge pass-zero omits Claude
    brainstorm.merge_pass_zero(tmp_path, models, "caching")
    shared = brainstorm.shared_path(tmp_path, "caching").read_text(encoding="utf-8")
    assert "## Codex" in shared
    assert "## Claude" not in shared


def test_codex_timeout_is_classified_printed_and_omitted(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    events: list[brainstorm.ProgressEvent] = []
    printed: list[str] = []
    status_map: dict[str, str] = {}

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if agent == "codex":
            raise agents.AgentTimeout("codex exceeded 300s and was terminated")
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text("claude findings\n", encoding="utf-8")
        return _ok(agent, "claude findings")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path,
        models,
        "timeout-topic",
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        progress_fn=events.append,
        status_map=status_map,
    )

    # 1. Codex omitted from successful-research map
    assert actual == {"claude": "claude"}
    assert "codex" not in actual

    # 2. Status map classifies Codex as timeout
    assert status_map["codex"].status == "failed"
    assert status_map["codex"].reason == brainstorm.FAILURE_TIMEOUT
    assert status_map["claude"].status == "succeeded"
    assert status_map["claude"].reason is None

    # Persisted round-trip
    persisted_status = brainstorm.load_status_map(tmp_path, "timeout-topic")
    assert persisted_status["codex"].status == "failed"
    assert persisted_status["codex"].reason == brainstorm.FAILURE_TIMEOUT
    assert persisted_status["claude"].status == "succeeded"
    assert persisted_status["claude"].reason is None
    assert persisted_status == status_map

    # Progress event
    codex_terminal = [e for e in events if e.agent == "codex" and e.status in ("failed", "skipped")][0]
    assert codex_terminal.status == "failed"
    assert codex_terminal.reason == brainstorm.FAILURE_TIMEOUT

    # 3. Printed lines include timeout detail
    assert any("Codex could not research this pass: timeout: codex exceeded 300s" in line for line in printed)
    assert any("[2/2] Codex failed pass-zero" in line and "timeout" in line for line in printed)

    # 4. Merge pass-zero omits Codex
    brainstorm.merge_pass_zero(tmp_path, models, "timeout-topic")
    shared = brainstorm.shared_path(tmp_path, "timeout-topic").read_text(encoding="utf-8")
    assert "## Claude" in shared
    assert "## Codex" not in shared


def test_missing_agent_is_classified_printed_and_omitted(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("grok", "Grok")]
    events: list[brainstorm.ProgressEvent] = []
    printed: list[str] = []
    status_map: dict[str, str] = {}

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if agent == "grok":
            raise agents.AgentMissing("grok is not installed or not on PATH")
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text("claude findings\n", encoding="utf-8")
        return _ok(agent, "claude findings")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path,
        models,
        "missing-topic",
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        progress_fn=events.append,
        status_map=status_map,
    )

    # 1. Grok omitted from actual_agents
    assert actual == {"claude": "claude"}
    assert "grok" not in actual

    # 2. Status map classifies Grok as missing executable
    assert status_map["grok"].status == "skipped"
    assert status_map["grok"].reason == brainstorm.FAILURE_MISSING
    assert status_map["claude"].status == "succeeded"
    assert status_map["claude"].reason is None

    # Persisted round-trip
    persisted_status = brainstorm.load_status_map(tmp_path, "missing-topic")
    assert persisted_status["grok"].status == "skipped"
    assert persisted_status["grok"].reason == brainstorm.FAILURE_MISSING
    assert persisted_status["claude"].status == "succeeded"
    assert persisted_status["claude"].reason is None
    assert persisted_status == status_map

    # Progress event
    grok_terminal = [e for e in events if e.agent == "grok" and e.status in ("failed", "skipped")][0]
    assert grok_terminal.status == "skipped"
    assert grok_terminal.reason == brainstorm.FAILURE_MISSING

    # 3. Printed lines
    assert any("Grok could not research this pass: missing executable: grok is not installed or not on PATH" in line for line in printed)
    assert any("[2/2] Grok skipped pass-zero" in line and "missing executable" in line for line in printed)

    # 4. Merge pass-zero omits Grok
    brainstorm.merge_pass_zero(tmp_path, models, "missing-topic")
    shared = brainstorm.shared_path(tmp_path, "missing-topic").read_text(encoding="utf-8")
    assert "## Claude" in shared
    assert "## Grok" not in shared


def test_generic_failure_is_classified_printed_and_omitted(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    printed: list[str] = []
    events: list[brainstorm.ProgressEvent] = []
    status_map: dict[str, str] = {}

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if agent == "claude":
            # Simulate a dirty temp file left by a crashed turn
            tpath = brainstorm.temp_path(root, agent)
            tpath.parent.mkdir(parents=True, exist_ok=True)
            tpath.write_text("partial corrupted output\n", encoding="utf-8")
            return {
                "agent": "claude",
                "response": "unexpected segmentation fault (core dumped)",
                "rate_limited": False,
                "ok": False,
            }
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text("codex findings\n", encoding="utf-8")
        return _ok(agent, "codex findings")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path,
        models,
        "generic-topic",
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        progress_fn=events.append,
        status_map=status_map,
    )

    # 1. Claude omitted
    assert actual == {"codex": "codex"}
    assert "claude" not in actual

    # 2. Status map classifies Claude as generic failure
    assert status_map["claude"].status == "failed"
    assert status_map["claude"].reason == brainstorm.FAILURE_GENERIC
    assert status_map["codex"].status == "succeeded"
    assert status_map["codex"].reason is None

    # Persisted round-trip
    persisted_status = brainstorm.load_status_map(tmp_path, "generic-topic")
    assert persisted_status["claude"].status == "failed"
    assert persisted_status["claude"].reason == brainstorm.FAILURE_GENERIC
    assert persisted_status["codex"].status == "succeeded"
    assert persisted_status["codex"].reason is None
    assert persisted_status == status_map

    # Progress event
    claude_terminal = [e for e in events if e.agent == "claude" and e.status in ("failed", "skipped")][0]
    assert claude_terminal.status == "failed"
    assert claude_terminal.reason == brainstorm.FAILURE_GENERIC

    # 3. Printed lines
    assert "[1/2] Claude failed pass-zero (0s): generic non-zero failure" in printed
    assert "Claude could not research this pass: generic non-zero failure" in printed

    # 4. Merge pass-zero omits Claude and cleans up partial temp file
    brainstorm.merge_pass_zero(tmp_path, models, "generic-topic")
    shared = brainstorm.shared_path(tmp_path, "generic-topic").read_text(encoding="utf-8")
    assert "## Codex" in shared
    assert "## Claude" not in shared
    assert not brainstorm.temp_path(tmp_path, "claude").exists()


def test_successful_substitution_from_backup_chain_is_preserved(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    status_map: dict[str, str] = {}

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        # claude slot failed over to codex and succeeded
        actual = "codex" if agent == "claude" else agent
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"findings by {actual}\n", encoding="utf-8")
        return {
            "agent": actual,
            "response": f"response from {actual}",
            "ok": True,
            "rate_limited": False,
        }

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path,
        models,
        "backup-topic",
        settings=settings,
        status_map=status_map,
    )

    # Successful substitution from backup chain is preserved
    assert actual == {"claude": "codex", "codex": "codex"}
    assert status_map["claude"].status == "succeeded"
    assert status_map["claude"].reason is None
    assert status_map["codex"].status == "succeeded"
    assert status_map["codex"].reason is None

    # Persisted round-trip
    persisted = brainstorm.load_status_map(tmp_path, "backup-topic")
    assert persisted["claude"].status == "succeeded"
    assert persisted["codex"].status == "succeeded"
    assert persisted == status_map

    # Merge attributes correctly to Codex
    brainstorm.merge_pass_zero(tmp_path, models, "backup-topic")
    shared = brainstorm.shared_path(tmp_path, "backup-topic").read_text(encoding="utf-8")
    assert "## Codex" in shared
    assert "## Claude" not in shared
    assert shared.count("## Codex") == 2


def test_status_map_json_round_trip(tmp_path: Path):
    mapping = {
        "claude": brainstorm.AgentStatus("failed", brainstorm.FAILURE_RATE_LIMIT),
        "codex": brainstorm.AgentStatus("failed", brainstorm.FAILURE_TIMEOUT),
        "grok": brainstorm.AgentStatus("skipped", brainstorm.FAILURE_MISSING),
        "antigravity": brainstorm.AgentStatus("succeeded", None),
    }
    brainstorm.save_status_map(tmp_path, mapping, "round-trip-topic")
    loaded = brainstorm.load_status_map(tmp_path, "round-trip-topic")
    assert loaded == mapping
    assert loaded["claude"].status == "failed"
    assert loaded["claude"].reason == brainstorm.FAILURE_RATE_LIMIT
    assert loaded["codex"].status == "failed"
    assert loaded["codex"].reason == brainstorm.FAILURE_TIMEOUT
    assert loaded["grok"].status == "skipped"
    assert loaded["grok"].reason == brainstorm.FAILURE_MISSING
    assert loaded["antigravity"].status == "succeeded"
    assert loaded["antigravity"].reason is None


def test_agent_status_equality_is_not_aliased():
    status = brainstorm.AgentStatus("failed", brainstorm.FAILURE_TIMEOUT)
    assert status != "failed"
    assert status != "skipped"
    assert status != brainstorm.FAILURE_TIMEOUT
    assert status == brainstorm.AgentStatus("failed", brainstorm.FAILURE_TIMEOUT)
    assert status.status == "failed"
    assert status.reason == "timeout"


def test_review_pass_skips_pass_zero_failures_and_preserves_substitutions(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("grok", "Grok"), ("agy", "Antigravity")]
    events: list[brainstorm.ProgressEvent] = []
    calls: list[tuple[str, str]] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        calls.append((agent, prompt))
        if "independently" in prompt:
            # Pass zero
            if agent == "claude":
                # claude slot substituted by codex
                tpath = brainstorm.temp_path(root, agent)
                tpath.parent.mkdir(parents=True, exist_ok=True)
                tpath.write_text("claude research by codex\n", encoding="utf-8")
                return {"agent": "codex", "response": "ok", "ok": True, "rate_limited": False}
            elif agent == "grok":
                # grok fails with rate limit
                return {
                    "agent": "grok",
                    "response": "quota exhausted: rate limit exceeded",
                    "ok": False,
                    "rate_limited": True,
                }
            elif agent == "agy":
                tpath = brainstorm.temp_path(root, agent)
                tpath.parent.mkdir(parents=True, exist_ok=True)
                tpath.write_text("agy research\n", encoding="utf-8")
                return _ok(agent, "agy research")
        elif "Combined review pass" in prompt:
            actual = "codex" if agent == "claude" else agent
            return _ok(actual, "revised")
        return _ok(agent, "ok")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)

    # 1. Run pass-zero
    actual = brainstorm.run_pass_zero(
        tmp_path,
        models,
        "review-filter-topic",
        settings=settings,
        progress_fn=events.append,
    )
    assert actual == {"claude": "codex", "agy": "agy"}
    assert "grok" not in actual

    # 2. Merge pass-zero
    brainstorm.merge_pass_zero(tmp_path, models, "review-filter-topic", actual_agents=actual)
    shared = brainstorm.shared_path(tmp_path, "review-filter-topic").read_text(encoding="utf-8")
    assert "## Codex" in shared
    assert "## Antigravity" in shared
    assert "## Grok" not in shared

    # 3. Run review pass 1
    reviewed = brainstorm.run_review_pass(
        tmp_path,
        models,
        "review-filter-topic",
        1,
        settings=settings,
        progress_fn=events.append,
        actual_agents=actual,
    )

    # Failed agent (grok) received no review calls
    review_calls = [agent for agent, prompt in calls if "Combined review pass" in prompt]
    assert "grok" not in review_calls
    assert review_calls == ["claude", "agy"]

    claude_review_prompt = [prompt for agent, prompt in calls if agent == "claude" and "Combined review pass" in prompt][0]
    assert '("## Codex")' in claude_review_prompt

    # Failed agent (grok) received no review lifecycle events
    review_events = [e for e in events if e.phase == brainstorm.PHASE_REVIEW]
    assert not any(e.agent == "grok" for e in review_events)
    assert [e.agent for e in review_events] == ["claude", "claude", "claude", "agy", "agy", "agy"]

    # Review event ordinals and totals reflect active reviewing models (2 models, not 3)
    assert all(e.total == 2 for e in review_events)

    # Successful substitution (claude -> codex) preserved in review return map
    assert reviewed == {"claude": "codex", "agy": "agy"}
    assert "grok" not in reviewed

    # Persisted map round-trip check
    persisted = brainstorm._load_actual_agents(tmp_path, "review-filter-topic")
    assert persisted == {"claude": "codex", "agy": "agy"}
    assert "grok" not in persisted
