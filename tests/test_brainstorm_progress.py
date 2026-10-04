import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from whyline_relay import agents, brainstorm, chat, chatlog, config, gitcheck


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
    brainstorm.shared_path(tmp_path, "topic").parent.mkdir(parents=True, exist_ok=True)
    brainstorm.shared_path(tmp_path, "topic").write_text(
        "# Brainstorm: topic\n\n## Claude\n\nresearch\n", encoding="utf-8"
    )
    brainstorm.run_final_synthesis(
        tmp_path, "claude", models, "topic", settings=settings,
        print_fn=lambda *a, **k: None, progress_fn=events.append,
        actual_agents=reviewed,
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
        ("starting", "claude", "synthesis", 1, None),
        ("running", "claude", "synthesis", 1, None),
        ("succeeded", "claude", "synthesis", 1, None),
    ]
    assert events[5].reason == "timeout — codex exceeded 300s"
    assert events[8].reason == "generic non-zero failure — nope"
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
    brainstorm.shared_path(tmp_path, "topic").parent.mkdir(parents=True, exist_ok=True)
    brainstorm.shared_path(tmp_path, "topic").write_text(
        "# Brainstorm: topic\n\n## Claude\n\nresearch\n", encoding="utf-8"
    )
    printed: list[str] = []
    brainstorm.run_pass_zero(
        tmp_path, models, "topic", settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    assert printed == [
        "[1/2] Claude starting pass-zero (0s)",
        "[1/2] Claude succeeded pass-zero (2m5s)",
        "[2/2] Codex starting pass-zero (0s)",
        "[2/2] Codex skipped pass-zero (4s): missing executable — codex is not installed",
        "Codex could not research this pass: missing executable — codex is not installed",
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
    brainstorm.shared_path(tmp_path, "topic").parent.mkdir(parents=True, exist_ok=True)
    brainstorm.shared_path(tmp_path, "topic").write_text(
        "# Brainstorm: topic\n\n## Claude\n\nresearch\n", encoding="utf-8"
    )
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
    brainstorm.shared_path(tmp_path, "topic").parent.mkdir(parents=True, exist_ok=True)
    brainstorm.shared_path(tmp_path, "topic").write_text(
        "# Brainstorm: topic\n\n## Claude\n\nresearch\n", encoding="utf-8"
    )
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
        "No selected agent succeeded for final synthesis. Check agent configurations, quotas, or credentials and try again.",
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

    brainstorm.shared_path(tmp_path, "topic").parent.mkdir(parents=True, exist_ok=True)
    brainstorm.shared_path(tmp_path, "topic").write_text(
        "# Brainstorm: topic\n\n## Claude\n\nresearch\n", encoding="utf-8"
    )
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
        "",  # timeout default (15m)
        "/exit",
    ])

    def fake_run_fn(command, prompt, **kwargs):
        tpath = brainstorm.temp_path(tmp_path, "claude")
        if not tpath.exists():
            tpath.parent.mkdir(parents=True, exist_ok=True)
            tpath.write_text("claude findings\n", encoding="utf-8")
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
    assert claude_terminal.reason == "quota/rate-limit — You have exceeded your usage limit. Try again later."

    # 4. Printed output includes start/finish lines and pass-zero explanation
    assert "[1/2] Claude failed pass-zero (0s): quota/rate-limit — You have exceeded your usage limit. Try again later." in printed
    assert "Claude could not research this pass: quota/rate-limit — You have exceeded your usage limit. Try again later." in printed
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
    assert codex_terminal.reason == "timeout — codex exceeded 300s and was terminated"

    # 3. Printed lines include timeout detail
    assert any("Codex could not research this pass: timeout — codex exceeded 300s" in line for line in printed)
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
    assert grok_terminal.reason == "missing executable — grok is not installed or not on PATH"

    # 3. Printed lines
    assert any("Grok could not research this pass: missing executable — grok is not installed or not on PATH" in line for line in printed)
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
    assert claude_terminal.reason == "generic non-zero failure — unexpected segmentation fault (core dumped)"

    # 3. Printed lines
    assert "[1/2] Claude failed pass-zero (0s): generic non-zero failure — unexpected segmentation fault (core dumped)" in printed
    assert "Claude could not research this pass: generic non-zero failure — unexpected segmentation fault (core dumped)" in printed

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


def test_final_agent_fallback_when_requested_agent_fails_at_runtime(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    events: list[brainstorm.ProgressEvent] = []
    printed: list[str] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "Final Synthesis" in prompt:
            if agent == "claude":
                return {
                    "agent": "claude",
                    "response": "rate limit exceeded: quota exhausted",
                    "rate_limited": True,
                    "ok": False,
                }
            shared = brainstorm.shared_path(root, "runtime-fallback-topic")
            content = shared.read_text(encoding="utf-8")
            shared.write_text(
                content + "\n## Final Synthesis\n\nCodex recommendation\n",
                encoding="utf-8",
            )
            return _ok("codex", "Codex recommendation")
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"{agent} research\n", encoding="utf-8")
        return _ok(agent, f"{agent} research")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path, models, "runtime-fallback-topic", settings=settings
    )
    brainstorm.merge_pass_zero(
        tmp_path, models, "runtime-fallback-topic", actual_agents=actual
    )
    record = brainstorm.run_final_synthesis(
        tmp_path,
        "claude",
        models,
        "runtime-fallback-topic",
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        progress_fn=events.append,
        actual_agents=actual,
    )

    # 1. Claude failed at runtime, Codex succeeded
    assert record["agent"] == "codex"
    assert record["ok"] is True
    assert record["response"] == "Codex recommendation"

    # 2. Progress events show Claude failing and Codex running and succeeding
    synth_events = [e for e in events if e.phase == brainstorm.PHASE_SYNTHESIS]
    assert [(e.agent, e.status) for e in synth_events] == [
        ("claude", "starting"),
        ("claude", "running"),
        ("claude", "failed"),
        ("codex", "starting"),
        ("codex", "running"),
        ("codex", "succeeded"),
    ]
    assert synth_events[2].reason == "quota/rate-limit — rate limit exceeded: quota exhausted"

    # 3. Substitution was announced
    assert any(
        "Claude failed at runtime; substituting Codex for final synthesis." in line
        for line in printed
    )

    # 4. Final synthesis section in shared doc exists and was written by Codex
    shared = brainstorm.shared_path(tmp_path, "runtime-fallback-topic").read_text(
        encoding="utf-8"
    )
    assert "## Final Synthesis" in shared
    assert "Codex recommendation" in shared


def test_final_agent_fallback_when_requested_agent_failed_in_pass_zero(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    events: list[brainstorm.ProgressEvent] = []
    printed: list[str] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "independently" in prompt and agent == "claude":
            raise agents.AgentTimeout("claude turn timed out after 300s")
        if "Final Synthesis" in prompt:
            shared = brainstorm.shared_path(root, "pass0-fallback-topic")
            content = shared.read_text(encoding="utf-8")
            shared.write_text(
                content + "\n## Final Synthesis\n\nCodex recommendation\n",
                encoding="utf-8",
            )
            return _ok("codex", "Codex recommendation")
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"{agent} research\n", encoding="utf-8")
        return _ok(agent, f"{agent} research")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path, models, "pass0-fallback-topic", settings=settings
    )
    assert actual == {"codex": "codex"}
    brainstorm.merge_pass_zero(
        tmp_path, models, "pass0-fallback-topic", actual_agents=actual
    )

    # Claude was requested, but failed in pass 0
    record = brainstorm.run_final_synthesis(
        tmp_path,
        "claude",
        models,
        "pass0-fallback-topic",
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        progress_fn=events.append,
        actual_agents=actual,
    )

    # 1. Claude was never invoked for final synthesis
    synth_events = [e for e in events if e.phase == brainstorm.PHASE_SYNTHESIS]
    assert not any(e.agent == "claude" for e in synth_events)
    assert [(e.agent, e.status) for e in synth_events] == [
        ("codex", "starting"),
        ("codex", "running"),
        ("codex", "succeeded"),
    ]

    # 2. Substitution was announced
    assert any(
        "Claude failed at runtime; substituting Codex for final synthesis." in line
        for line in printed
    )
    assert record["agent"] == "codex"


def test_all_agents_failed_in_pass_zero_stops_without_empty_synthesis(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    chat.save_default_agent(tmp_path, "claude")
    models = [("claude", "Claude"), ("codex", "Codex")]

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if agent == "claude":
            return {
                "agent": "claude",
                "response": "quota exhausted",
                "rate_limited": True,
                "ok": False,
            }
        raise agents.AgentTimeout("codex timed out")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    answers = iter([
        "/brainstorm",
        "all-failed-topic",
        "1,2",  # Claude, Codex
        "1",    # 1 pass
        "claude",
        "",     # timeout default (15m)
        "/exit",
    ])
    printed: list[str] = []
    chat.repl(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        which=lambda name: "/bin/x",
    )

    # 1. Actionable stopping message printed
    assert any(
        "No selected agent succeeded; stopping brainstorm without synthesis." in line
        for line in printed
    )
    assert any(
        "Check agent availability, authentication, or quotas and try again." in line
        for line in printed
    )

    # 2. No shared doc or empty synthesis file was created
    shared = brainstorm.shared_path(tmp_path, "all-failed-topic")
    assert not shared.exists()

    # 3. No review passes or final synthesis ran
    assert not any("starting review pass" in line for line in printed)
    assert not any("starting final synthesis" in line for line in printed)


def test_all_agents_fail_during_final_synthesis_stops_without_empty_synthesis(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    events: list[brainstorm.ProgressEvent] = []
    printed: list[str] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "Final Synthesis" in prompt:
            # Both fail during final synthesis
            return {
                "agent": agent,
                "response": f"{agent} internal error",
                "rate_limited": False,
                "ok": False,
            }
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"{agent} research\n", encoding="utf-8")
        return _ok(agent, f"{agent} research")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path, models, "synth-all-fail-topic", settings=settings
    )
    brainstorm.merge_pass_zero(
        tmp_path, models, "synth-all-fail-topic", actual_agents=actual
    )

    record = brainstorm.run_final_synthesis(
        tmp_path,
        "claude",
        models,
        "synth-all-fail-topic",
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        progress_fn=events.append,
        actual_agents=actual,
    )

    # 1. Returned record reports failure
    assert record["ok"] is False

    # 2. Both agents attempted final synthesis and failed
    synth_events = [e for e in events if e.phase == brainstorm.PHASE_SYNTHESIS]
    assert [(e.agent, e.status) for e in synth_events] == [
        ("claude", "starting"),
        ("claude", "running"),
        ("claude", "failed"),
        ("codex", "starting"),
        ("codex", "running"),
        ("codex", "failed"),
    ]

    # 3. Actionable message printed
    assert any(
        "No selected agent succeeded for final synthesis." in line
        for line in printed
    )

    # 4. No empty or broken ## Final Synthesis in shared doc
    shared = brainstorm.shared_path(tmp_path, "synth-all-fail-topic").read_text(
        encoding="utf-8"
    )
    assert "## Final Synthesis" not in shared


def test_review_pass_preserves_section_and_attribution_on_failure(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    printed: list[str] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "Combined review pass" in prompt:
            if agent == "claude":
                # Claude mutates/corrupts the shared file before failing
                shared = brainstorm.shared_path(root, "review-preserve-topic")
                shared.write_text("# Corrupted by Claude\n\nAll sections deleted!", encoding="utf-8")
                gitcheck.commit_all(root, "claude corrupted file")
                return {
                    "agent": "claude",
                    "response": "unexpected crash",
                    "rate_limited": False,
                    "ok": False,
                }
            # Codex succeeds review and revises
            shared = brainstorm.shared_path(root, "review-preserve-topic")
            content = shared.read_text(encoding="utf-8")
            revised = content.replace("Codex research", "Codex revised research")
            shared.write_text(revised, encoding="utf-8")
            return _ok("codex", "revised")
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"{agent.title()} research\n", encoding="utf-8")
        return _ok(agent, f"{agent.title()} research")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path, models, "review-preserve-topic", settings=settings
    )
    brainstorm.merge_pass_zero(
        tmp_path, models, "review-preserve-topic", actual_agents=actual
    )
    shared_initial = brainstorm.shared_path(tmp_path, "review-preserve-topic").read_text(
        encoding="utf-8"
    )
    assert "## Claude\n\nClaude research" in shared_initial
    assert "## Codex\n\nCodex research" in shared_initial

    reviewed = brainstorm.run_review_pass(
        tmp_path,
        models,
        "review-preserve-topic",
        1,
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        actual_agents=actual,
    )

    # 1. Claude failure warning was printed
    assert any("Claude's review pass 1 reported a failure." in line for line in printed)

    # 2. Claude's section is preserved untouched in shared doc despite corruption
    shared_after = brainstorm.shared_path(tmp_path, "review-preserve-topic").read_text(
        encoding="utf-8"
    )
    assert "Corrupted by Claude" not in shared_after
    assert "## Claude\n\nClaude research" in shared_after
    # Codex's section was updated
    assert "Codex revised research" in shared_after

    # 3. Attribution in returned map is preserved
    assert reviewed == {"claude": "claude", "codex": "codex"}


def test_setup_brainstorm_updates_attribution_on_synthesizer_fallback(
    tmp_path: Path, monkeypatch
):
    from whyline_relay import setup, planner
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    seen_drafted_by = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "plan.md at" in prompt:
            draft_path = brainstorm.plan_draft_path(root)
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(
                "# Plan\n\n- [ ] TSK-1: implement feature\n    Indented detail\n",
                encoding="utf-8",
            )
            return _ok(agent, "drafted plan")
        if "Final Synthesis" in prompt:
            # Claude fails final synthesis; Codex will substitute
            if agent == "claude":
                return {
                    "agent": "claude",
                    "response": "rate limited",
                    "rate_limited": True,
                    "ok": False,
                }
            shared = brainstorm.shared_path(root, "setup-attribution-topic")
            content = shared.read_text(encoding="utf-8")
            shared.write_text(content + "\n## Final Synthesis\n\nPlan here\n", encoding="utf-8")
            return _ok("codex", "Plan here")
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"{agent} research\n", encoding="utf-8")
        return _ok(agent, "ok")

    def fake_review_gate(root, settings, draft, topic, *, drafted_by, revise_fn, confirm):
        seen_drafted_by.append(drafted_by)
        (root / settings.plan).write_text(draft.read_text(encoding="utf-8"))
        return "approved"

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    monkeypatch.setattr(planner, "review_gate", fake_review_gate)

    answers = iter([
        "brainstorm",
        "setup-attribution-topic",
        "1,2",  # Claude, Codex
            "0",    # 0 review passes
            "claude",  # requested final agent
            "",      # timeout default (15m)
        ])
    printed: list[str] = []
    res = setup.choose_plan_source(
        tmp_path,
        settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        confirm=lambda prompt="": True,
    )
    assert res is True
    # Attribution reflected the substituted agent (Codex), NOT Claude!
    assert seen_drafted_by == ["brainstorm (codex)"]


def test_fallback_skips_agent_reporting_success_without_writing_research(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    printed: list[str] = []
    synthesis_agents_invoked = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "Final Synthesis" in prompt:
            synthesis_agents_invoked.append(agent)
            shared = brainstorm.shared_path(root, "no-research-success-topic")
            content = shared.read_text(encoding="utf-8")
            shared.write_text(content + "\n## Final Synthesis\n\nCodex plan\n", encoding="utf-8")
            return _ok(agent, "Codex plan")
        # Pass 0:
        if agent == "claude":
            # Claude reports success but writes no temp file / research
            return _ok("claude", "I am done without writing files")
        # Codex writes research
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text("Codex valid research\n", encoding="utf-8")
        return _ok(agent, "Codex valid research")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path, models, "no-research-success-topic", settings=settings
    )
    # run_turn reported ok for both, so actual initially contains both
    assert "claude" in actual
    assert "codex" in actual

    # merge_pass_zero skips Claude because no non-empty temp file exists
    brainstorm.merge_pass_zero(
        tmp_path, models, "no-research-success-topic", actual_agents=actual
    )

    shared = brainstorm.shared_path(tmp_path, "no-research-success-topic").read_text(
        encoding="utf-8"
    )
    assert "## Claude" not in shared
    assert "## Codex" in shared

    # Run final synthesis requesting claude
    record = brainstorm.run_final_synthesis(
        tmp_path,
        "claude",
        models,
        "no-research-success-topic",
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        actual_agents=actual,
    )

    assert record["ok"] is True
    assert record["agent"] == "codex"
    # Claude was skipped because it had no usable research; Codex was substituted
    assert synthesis_agents_invoked == ["codex"]
    assert any(
        "Claude failed at runtime; substituting Codex for final synthesis." in line
        for line in printed
    )


def test_review_pass_restores_shared_doc_on_exception_during_turn(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    printed: list[str] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "Combined review pass" in prompt:
            if agent == "claude":
                # Claude corrupts the shared file and raises an exception
                shared = brainstorm.shared_path(root, "review-exc-topic")
                shared.write_text("# Corrupted before exception", encoding="utf-8")
                gitcheck.commit_all(root, "claude corrupted file")
                raise agents.AgentTimeout("Claude timed out during review")
            return _ok("codex", "codex review ok")
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"{agent} research\n", encoding="utf-8")
        return _ok(agent, "research")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path, models, "review-exc-topic", settings=settings
    )
    brainstorm.merge_pass_zero(
        tmp_path, models, "review-exc-topic", actual_agents=actual
    )

    reviewed = brainstorm.run_review_pass(
        tmp_path,
        models,
        "review-exc-topic",
        1,
        settings=settings,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        actual_agents=actual,
    )

    # 1. Claude timeout message was printed
    assert any("Claude could not review this pass" in line for line in printed)

    # 2. Shared doc was restored despite corruption before timeout
    shared_after = brainstorm.shared_path(tmp_path, "review-exc-topic").read_text(
        encoding="utf-8"
    )
    assert "Corrupted before exception" not in shared_after
    assert "## Claude\n\nclaude research" in shared_after

    # 3. Attribution preserved
    assert reviewed == {"claude": "claude", "codex": "codex"}


def test_all_agents_fail_by_exception_during_final_synthesis_prints_guidance(
    tmp_path: Path, monkeypatch
):
    import pytest
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    printed: list[str] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "Final Synthesis" in prompt:
            # Both fail by exception
            shared = brainstorm.shared_path(root, "synth-exc-topic")
            content = shared.read_text(encoding="utf-8")
            shared.write_text(content + "\n## Final Synthesis\n\npartial\n", encoding="utf-8")
            raise agents.AgentTimeout(f"{agent} timed out")
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"{agent} research\n", encoding="utf-8")
        return _ok(agent, f"{agent} research")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path, models, "synth-exc-topic", settings=settings
    )
    brainstorm.merge_pass_zero(
        tmp_path, models, "synth-exc-topic", actual_agents=actual
    )

    with pytest.raises(agents.AgentTimeout):
        brainstorm.run_final_synthesis(
            tmp_path,
            "claude",
            models,
            "synth-exc-topic",
            settings=settings,
            print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
            actual_agents=actual,
        )

    # 1. Substitution announcement was printed
    assert any(
        "Claude failed at runtime; substituting Codex for final synthesis." in line
        for line in printed
    )

    # 2. Actionable message was printed BEFORE raising
    assert any(
        "No selected agent succeeded for final synthesis. Check agent configurations, quotas, or credentials and try again."
        in line
        for line in printed
    )

    # 3. Partial Final Synthesis stripped from shared doc
    shared_after = brainstorm.shared_path(tmp_path, "synth-exc-topic").read_text(
        encoding="utf-8"
    )
    assert "## Final Synthesis" not in shared_after


def test_mixed_failures_during_final_synthesis_prints_guidance(
    tmp_path: Path, monkeypatch
):
    import pytest
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    printed: list[str] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "Final Synthesis" in prompt:
            if agent == "claude":
                # Claude fails with ok=False
                return {
                    "agent": "claude",
                    "response": "rate limit error",
                    "rate_limited": True,
                    "ok": False,
                }
            # Codex fails with AgentTimeout exception
            raise agents.AgentTimeout("codex timed out")
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"{agent} research\n", encoding="utf-8")
        return _ok(agent, f"{agent} research")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    actual = brainstorm.run_pass_zero(
        tmp_path, models, "synth-mixed-topic", settings=settings
    )
    brainstorm.merge_pass_zero(
        tmp_path, models, "synth-mixed-topic", actual_agents=actual
    )

    with pytest.raises(agents.AgentTimeout):
        brainstorm.run_final_synthesis(
            tmp_path,
            "claude",
            models,
            "synth-mixed-topic",
            settings=settings,
            print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
            actual_agents=actual,
        )

    # 1. Substitution announcement was printed
    assert any(
        "Claude failed at runtime; substituting Codex for final synthesis." in line
        for line in printed
    )

    # 2. Actionable message was printed BEFORE raising
    assert any(
        "No selected agent succeeded for final synthesis. Check agent configurations, quotas, or credentials and try again."
        in line
        for line in printed
    )

    # 3. Partial Final Synthesis stripped from shared doc
    shared_after = brainstorm.shared_path(tmp_path, "synth-mixed-topic").read_text(
        encoding="utf-8"
    )
    assert "## Final Synthesis" not in shared_after


def test_format_progress_table_empty():
    assert brainstorm.format_progress_table([]) == ""


def test_format_progress_table_layout_and_fields():
    events = [
        brainstorm.ProgressEvent(
            status="succeeded",
            agent="claude",
            label="Claude",
            phase=brainstorm.PHASE_PASS_ZERO,
            ordinal=1,
            total=2,
            elapsed_seconds=1.2,
        ),
        brainstorm.ProgressEvent(
            status="failed",
            agent="codex",
            label="Codex",
            phase=brainstorm.PHASE_PASS_ZERO,
            ordinal=2,
            total=2,
            elapsed_seconds=300.0,
            reason=brainstorm.FAILURE_TIMEOUT,
        ),
        brainstorm.ProgressEvent(
            status="succeeded",
            agent="claude",
            label="Claude",
            phase=brainstorm.PHASE_REVIEW,
            ordinal=1,
            total=1,
            elapsed_seconds=2.4,
            pass_number=1,
        ),
        brainstorm.ProgressEvent(
            status="skipped",
            agent="grok",
            label="Grok",
            phase=brainstorm.PHASE_REVIEW,
            ordinal=2,
            total=2,
            elapsed_seconds=0.1,
            pass_number=1,
            reason=brainstorm.FAILURE_MISSING,
        ),
        brainstorm.ProgressEvent(
            status="succeeded",
            agent="claude",
            label="Claude",
            phase=brainstorm.PHASE_SYNTHESIS,
            ordinal=1,
            total=1,
            elapsed_seconds=75.0,
        ),
    ]

    table = brainstorm.format_progress_table(events)
    lines = table.splitlines()

    # 1. Header row contains all five required column titles
    header = lines[0]
    for expected_col in ("Agent", "Phase", "State", "Elapsed Time", "Failure Reason"):
        assert expected_col in header

    # 2. Separator line follows header
    separator = lines[1]
    assert separator.startswith("------")
    assert "-" in separator

    # 3. Data rows contain agent, phase, state, formatted elapsed time, and reason
    row_claude_p0 = lines[2]
    assert "Claude" in row_claude_p0
    assert "pass-zero" in row_claude_p0
    assert "succeeded" in row_claude_p0
    assert "1s" in row_claude_p0
    assert "-" in row_claude_p0

    row_codex_p0 = lines[3]
    assert "Codex" in row_codex_p0
    assert "pass-zero" in row_codex_p0
    assert "failed" in row_codex_p0
    assert "5m0s" in row_codex_p0
    assert brainstorm.FAILURE_TIMEOUT in row_codex_p0

    row_claude_rev = lines[4]
    assert "Claude" in row_claude_rev
    assert "review pass 1" in row_claude_rev
    assert "succeeded" in row_claude_rev
    assert "2s" in row_claude_rev
    assert "-" in row_claude_rev

    row_grok_rev = lines[5]
    assert "Grok" in row_grok_rev
    assert "review pass 1" in row_grok_rev
    assert "skipped" in row_grok_rev
    assert "0s" in row_grok_rev
    assert brainstorm.FAILURE_MISSING in row_grok_rev

    row_claude_synth = lines[6]
    assert "Claude" in row_claude_synth
    assert "final synthesis" in row_claude_synth
    assert "succeeded" in row_claude_synth
    assert "1m15s" in row_claude_synth
    assert "-" in row_claude_synth


def test_format_progress_table_deduplicates_by_turn_by_default():
    # Stream of starting, running, succeeded for one turn
    events = [
        brainstorm.ProgressEvent(
            status="starting",
            agent="claude",
            label="Claude",
            phase=brainstorm.PHASE_PASS_ZERO,
            ordinal=1,
            total=1,
            elapsed_seconds=0.0,
        ),
        brainstorm.ProgressEvent(
            status="running",
            agent="claude",
            label="Claude",
            phase=brainstorm.PHASE_PASS_ZERO,
            ordinal=1,
            total=1,
            elapsed_seconds=0.0,
        ),
        brainstorm.ProgressEvent(
            status="succeeded",
            agent="claude",
            label="Claude",
            phase=brainstorm.PHASE_PASS_ZERO,
            ordinal=1,
            total=1,
            elapsed_seconds=1.5,
        ),
    ]

    # Deduplicated by default -> exactly 1 data row showing final status
    table_dedup = brainstorm.format_progress_table(events)
    lines_dedup = table_dedup.splitlines()
    assert len(lines_dedup) == 3  # header, separator, 1 data row
    assert "succeeded" in lines_dedup[2]
    assert "starting" not in table_dedup
    assert "running" not in table_dedup

    # deduplicate=False -> 3 data rows
    table_all = brainstorm.format_progress_table(events, deduplicate=False)
    lines_all = table_all.splitlines()
    assert len(lines_all) == 5  # header, separator, 3 data rows


def test_format_progress_table_accepts_serialized_event_dicts_for_tui():
    event = brainstorm.ProgressEvent(
        status="failed",
        agent="grok",
        label="Grok",
        phase=brainstorm.PHASE_PASS_ZERO,
        ordinal=1,
        total=1,
        elapsed_seconds=2.0,
        reason=brainstorm.FAILURE_RATE_LIMIT,
    )
    # Serialize to JSON string then parse back to dict (as a TUI process would)
    serialized_dict = json.loads(json.dumps(event.to_dict()))

    table_from_event = brainstorm.format_progress_table([event])
    table_from_dict = brainstorm.format_progress_table([serialized_dict])

    assert table_from_dict == table_from_event
    assert "Grok" in table_from_dict
    assert "failed" in table_from_dict
    assert brainstorm.FAILURE_RATE_LIMIT in table_from_dict


def test_progress_event_to_dict_and_from_dict_round_trip():
    event = brainstorm.ProgressEvent(
        status="failed",
        agent="codex",
        label="Codex",
        phase=brainstorm.PHASE_REVIEW,
        ordinal=2,
        total=3,
        elapsed_seconds=12.5,
        pass_number=2,
        reason=brainstorm.FAILURE_AUTH,
    )
    # 1. to_dict matches asdict
    assert event.to_dict() == asdict(event)

    # 2. All 9 fields are present and JSON serializable
    as_dict = event.to_dict()
    assert set(as_dict.keys()) == {
        "status",
        "agent",
        "label",
        "phase",
        "ordinal",
        "total",
        "elapsed_seconds",
        "pass_number",
        "reason",
    }
    json_bytes = json.dumps(as_dict)
    loaded = json.loads(json_bytes)

    # 3. from_dict reconstructs an equal ProgressEvent
    reconstructed = brainstorm.ProgressEvent.from_dict(loaded)
    assert reconstructed == event
    assert reconstructed.status == "failed"
    assert reconstructed.reason == brainstorm.FAILURE_AUTH
    assert reconstructed.pass_number == 2
    assert reconstructed.elapsed_seconds == 12.5

    # 4. Dictionary-like item and get access
    assert event["status"] == "failed"
    assert event["reason"] == brainstorm.FAILURE_AUTH
    assert event.get("phase") == brainstorm.PHASE_REVIEW
    assert event.get("nonexistent", "fallback") == "fallback"


def test_render_progress_table_invokes_print_fn():
    printed: list[str] = []
    events = [
        brainstorm.ProgressEvent(
            status="succeeded",
            agent="claude",
            label="Claude",
            phase=brainstorm.PHASE_PASS_ZERO,
            ordinal=1,
            total=1,
            elapsed_seconds=0.5,
        )
    ]
    brainstorm.render_progress_table(events, print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)))
    assert len(printed) == 1
    assert "Agent" in printed[0]
    assert "Claude" in printed[0]

    # Empty events does not print
    printed_empty: list[str] = []
    brainstorm.render_progress_table([], print_fn=lambda *a, **k: printed_empty.append(" ".join(str(x) for x in a)))
    assert printed_empty == []


def test_tui_event_stream_round_trip_end_to_end(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    stream: list[brainstorm.ProgressEvent] = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        if "independently" in prompt and agent == "codex":
            return {
                "agent": agent,
                "response": "rate limit error 429",
                "rate_limited": True,
                "ok": False,
            }
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"{agent} research\n", encoding="utf-8")
        return _ok(agent, f"{agent} ok")

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)

    actual = brainstorm.run_pass_zero(
        tmp_path, models, "tui-topic", settings=settings,
        print_fn=lambda *a, **k: None, progress_fn=stream.append,
    )
    brainstorm.merge_pass_zero(tmp_path, models, "tui-topic", actual_agents=actual)
    reviewed = brainstorm.run_review_pass(
        tmp_path, models, "tui-topic", 1, settings=settings,
        print_fn=lambda *a, **k: None, progress_fn=stream.append,
        actual_agents=actual,
    )
    brainstorm.run_final_synthesis(
        tmp_path, "claude", models, "tui-topic", settings=settings,
        print_fn=lambda *a, **k: None, progress_fn=stream.append,
        actual_agents=reviewed,
    )

    # All events are valid, serializable, and reconstructable
    serialized_stream = []
    for ev in stream:
        d = ev.to_dict()
        json_str = json.dumps(d)
        deserialized = brainstorm.ProgressEvent.from_dict(json.loads(json_str))
        assert deserialized == ev
        serialized_stream.append(d)

    # The table can be rendered directly from serialized payload dicts
    table = brainstorm.format_progress_table(serialized_stream)
    assert "Claude" in table
    assert "Codex" in table
    assert "pass-zero" in table
    assert "review pass 1" in table
    assert "final synthesis" in table
    assert brainstorm.FAILURE_RATE_LIMIT in table
