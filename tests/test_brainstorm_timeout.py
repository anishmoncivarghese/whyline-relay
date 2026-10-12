import json
import subprocess
import threading
from pathlib import Path

import pytest

from whyline_relay import agents, brainstorm, chat, config
from whyline_relay.agents import RunResult

# 45 minutes: one of the planned per-agent choices, and distinct from the
# 300-second compatibility default and the future 15-minute selector default.
SELECTED_SECONDS = 45 * 60
MODELS = [("claude", "Claude"), ("codex", "Codex")]
TOPIC = "my topic"


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def _recording_run(timeouts: list[int], *, fail: set[str] | None = None):
    """Fake agent runner that records the timeout it was given.

    `fail` names agents whose turn reports a non-zero exit, so a later
    substitute or backup still has to run.
    """
    failing = fail or set()

    def fake_run_fn(command, prompt, **kwargs):
        timeouts.append(kwargs["timeout_seconds"])
        if command[0] in failing:
            return RunResult(1, "You have exceeded your usage limit. Try again later.")
        text = "ok\n"
        if "-o" in command:
            Path(command[command.index("-o") + 1]).write_text(text, encoding="utf-8")
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    return fake_run_fn


def _commit_subjects(root: Path) -> str:
    return subprocess.run(
        ["git", "log", "--format=%s"], cwd=root,
        check=True, capture_output=True, text=True,
    ).stdout


def _write_research(root: Path) -> None:
    shared = brainstorm.shared_path(root, TOPIC)
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(
        f"# Brainstorm: {TOPIC}\n\n## Claude\n\nclaude research\n\n## Codex\n\ncodex research\n",
        encoding="utf-8",
    )


def test_chat_turn_passes_the_selected_timeout(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    timeouts: list[int] = []
    chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings,
        run_fn=_recording_run(timeouts), timeout_seconds=SELECTED_SECONDS,
    )
    assert timeouts == [SELECTED_SECONDS]


def test_chat_turn_backup_keeps_the_selected_timeout(tmp_path: Path):
    _init_repo(tmp_path)
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "config.toml").write_text('[backup]\nchain = ["codex"]\n')
    settings = config.load(tmp_path)
    timeouts: list[int] = []
    fake_login_ok = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    record = chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings,
        run_fn=_recording_run(timeouts, fail={"claude"}),
        runner=fake_login_ok, timeout_seconds=SELECTED_SECONDS,
    )
    assert record["agent"] == "codex"
    assert timeouts == [SELECTED_SECONDS, SELECTED_SECONDS]


def test_pass_zero_passes_the_selected_timeout_to_every_model(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    timeouts: list[int] = []
    brainstorm.run_pass_zero(
        tmp_path, MODELS, TOPIC, settings=settings,
        run_fn=_recording_run(timeouts), print_fn=lambda *a, **k: None,
        timeout_seconds=SELECTED_SECONDS,
    )
    assert timeouts == [SELECTED_SECONDS, SELECTED_SECONDS]


def test_review_pass_passes_the_selected_timeout_to_every_model(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_research(tmp_path)
    timeouts: list[int] = []
    brainstorm.run_review_pass(
        tmp_path, MODELS, TOPIC, 1, settings=settings,
        run_fn=_recording_run(timeouts), print_fn=lambda *a, **k: None,
        timeout_seconds=SELECTED_SECONDS,
    )
    assert timeouts == [SELECTED_SECONDS, SELECTED_SECONDS]


def test_final_synthesis_passes_the_selected_timeout_including_a_substitute(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_research(tmp_path)
    timeouts: list[int] = []
    record = brainstorm.run_final_synthesis(
        tmp_path, "claude", MODELS, TOPIC, settings=settings,
        run_fn=_recording_run(timeouts, fail={"claude"}),
        print_fn=lambda *a, **k: None, timeout_seconds=SELECTED_SECONDS,
    )
    assert record["agent"] == "codex"
    assert timeouts == [SELECTED_SECONDS, SELECTED_SECONDS]


def test_plan_draft_passes_the_selected_timeout_on_every_attempt(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_research(tmp_path)
    timeouts: list[int] = []
    attempts = {"n": 0}

    def fake_run_fn(command, prompt, **kwargs):
        timeouts.append(kwargs["timeout_seconds"])
        attempts["n"] += 1
        text = "not a plan\n" if attempts["n"] == 1 else "- [ ] T-1: do X\n  detail.\n"
        brainstorm.plan_draft_path(tmp_path).write_text(text, encoding="utf-8")
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", MODELS, TOPIC,
        run_fn=fake_run_fn, timeout_seconds=SELECTED_SECONDS,
    )
    assert timeouts == [SELECTED_SECONDS, SELECTED_SECONDS]


def test_omitted_timeout_keeps_the_chat_compatibility_default(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_research(tmp_path)
    assert chat.CHAT_TIMEOUT_SECONDS == 300

    chat_timeouts: list[int] = []
    chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings,
        run_fn=_recording_run(chat_timeouts),
    )
    assert chat_timeouts == [chat.CHAT_TIMEOUT_SECONDS]

    # A missing topic file is the 15 minute brainstorm default. It must not
    # fall through to Chat's 300 second compatibility limit.
    pass_zero: list[int] = []
    brainstorm.run_pass_zero(
        tmp_path, MODELS, TOPIC, settings=settings,
        run_fn=_recording_run(pass_zero), print_fn=lambda *a, **k: None,
    )
    assert pass_zero == [brainstorm.DEFAULT_TIMEOUT_SECONDS, brainstorm.DEFAULT_TIMEOUT_SECONDS]
    assert brainstorm.DEFAULT_TIMEOUT_SECONDS == 900
    assert not brainstorm.timeout_path(tmp_path, TOPIC).exists()

    review: list[int] = []
    brainstorm.run_review_pass(
        tmp_path, MODELS, TOPIC, 1, settings=settings,
        run_fn=_recording_run(review), print_fn=lambda *a, **k: None,
    )
    assert review == [brainstorm.DEFAULT_TIMEOUT_SECONDS, brainstorm.DEFAULT_TIMEOUT_SECONDS]

    synthesis: list[int] = []
    brainstorm.run_final_synthesis(
        tmp_path, "claude", MODELS, TOPIC, settings=settings,
        run_fn=_recording_run(synthesis), print_fn=lambda *a, **k: None,
    )
    assert synthesis == [brainstorm.DEFAULT_TIMEOUT_SECONDS]

    draft: list[int] = []

    def drafting_run(command, prompt, **kwargs):
        draft.append(kwargs["timeout_seconds"])
        brainstorm.plan_draft_path(tmp_path).write_text(
            "- [ ] T-1: do X\n  detail.\n", encoding="utf-8"
        )
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", MODELS, TOPIC, run_fn=drafting_run,
    )
    assert draft == [brainstorm.DEFAULT_TIMEOUT_SECONDS]

    revised: list[int] = []
    brainstorm.revise_synthesis(
        tmp_path, settings, "claude", MODELS, TOPIC, "tighten it",
        run_fn=_recording_run(revised),
    )
    assert revised == [brainstorm.DEFAULT_TIMEOUT_SECONDS]


def _timeout_file(root: Path, topic: str = TOPIC) -> Path:
    path = brainstorm.timeout_path(root, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_load_timeout_missing_truncated_key_and_non_positive(tmp_path: Path):
    assert brainstorm.load_timeout(tmp_path, TOPIC) is None

    path = _timeout_file(tmp_path)
    path.write_text('{"timeout_seconds":', encoding="utf-8")
    assert brainstorm.load_timeout(tmp_path, TOPIC) is None

    path.write_text('{"timeout_minutes": 45}', encoding="utf-8")
    assert brainstorm.load_timeout(tmp_path, TOPIC) is None

    path.write_text("900", encoding="utf-8")
    assert brainstorm.load_timeout(tmp_path, TOPIC) is None

    path.write_text('{"timeout_seconds": 0}', encoding="utf-8")
    assert brainstorm.load_timeout(tmp_path, TOPIC) is None

    path.write_text('{"timeout_seconds": -30}', encoding="utf-8")
    assert brainstorm.load_timeout(tmp_path, TOPIC) is None

    path.write_text('{"timeout_seconds": true}', encoding="utf-8")
    assert brainstorm.load_timeout(tmp_path, TOPIC) is None

    path.write_text('{"timeout_seconds": 900}', encoding="utf-8")
    path.chmod(0)
    try:
        assert brainstorm.load_timeout(tmp_path, TOPIC) is None
    finally:
        path.chmod(0o644)


def test_null_timeout_round_trip_writes_no_minutes_field(tmp_path: Path):
    brainstorm.save_timeout(tmp_path, chat.NO_LIMIT, TOPIC)
    path = brainstorm.timeout_path(tmp_path, TOPIC)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"timeout_seconds": None}
    assert "timeout_minutes" not in payload
    assert brainstorm.load_timeout(tmp_path, TOPIC) is chat.NO_LIMIT

    brainstorm.save_timeout(tmp_path, 2700, TOPIC)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == {"timeout_seconds": 2700, "timeout_minutes": 45}
    assert brainstorm.load_timeout(tmp_path, TOPIC) == 2700


def test_explicit_no_limit_is_passed_through_not_replaced(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_research(tmp_path)
    timeouts: list = []
    brainstorm.run_pass_zero(
        tmp_path, MODELS, TOPIC, settings=settings,
        run_fn=_recording_run(timeouts), print_fn=lambda *a, **k: None,
        timeout_seconds=chat.NO_LIMIT,
    )
    # Chat maps NO_LIMIT to an uncapped wait. A bare None would have been 300.
    assert timeouts == [None, None]
    payload = json.loads(brainstorm.timeout_path(tmp_path, TOPIC).read_text(encoding="utf-8"))
    assert payload == {"timeout_seconds": None}


def test_omitted_timeout_loads_a_stored_limit_or_null(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_research(tmp_path)

    brainstorm.save_timeout(tmp_path, SELECTED_SECONDS, TOPIC)
    stored: list = []
    brainstorm.run_pass_zero(
        tmp_path, MODELS, TOPIC, settings=settings,
        run_fn=_recording_run(stored), print_fn=lambda *a, **k: None,
    )
    assert stored == [SELECTED_SECONDS, SELECTED_SECONDS]

    brainstorm.save_timeout(tmp_path, chat.NO_LIMIT, TOPIC)
    unlimited: list = []
    brainstorm.run_review_pass(
        tmp_path, MODELS, TOPIC, 1, settings=settings,
        run_fn=_recording_run(unlimited), print_fn=lambda *a, **k: None,
    )
    assert unlimited == [None, None]
    payload = json.loads(brainstorm.timeout_path(tmp_path, TOPIC).read_text(encoding="utf-8"))
    assert payload == {"timeout_seconds": None}


def test_cancel_between_models_keeps_the_earlier_commit(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    cancel = threading.Event()
    called: list[str] = []

    def run_fn(command, prompt, **kwargs):
        agent = kwargs.get("agent_name") or command[0]
        called.append(agent)
        target = brainstorm.temp_path(tmp_path, agent)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"research by {agent}\n", encoding="utf-8")
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    def progress(event):
        if event.status == "succeeded":
            cancel.set()

    result = brainstorm.run_pass_zero(
        tmp_path, MODELS, TOPIC, settings=settings,
        run_fn=run_fn, print_fn=lambda *a, **k: None,
        progress_fn=progress, cancel_event=cancel,
    )
    assert called == ["claude"]
    assert result == {"claude": "claude"}
    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    ).stdout
    assert 'brainstorm: claude independent research on "my topic"' in log
    assert "codex independent research" not in log


def test_cancel_during_a_pass_zero_model_returns_finished_agents(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex"), ("grok", "Grok")]
    cancel = threading.Event()
    called: list[str] = []

    def run_fn(command, prompt, **kwargs):
        agent = kwargs.get("agent_name") or command[0]
        called.append(agent)
        target = brainstorm.temp_path(tmp_path, agent)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"research by {agent}\n", encoding="utf-8")
        if agent == "codex":
            cancel.set()
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    result = brainstorm.run_pass_zero(
        tmp_path, models, TOPIC, settings=settings,
        run_fn=run_fn, print_fn=lambda *a, **k: None, cancel_event=cancel,
    )
    assert called == ["claude", "codex"]
    assert result == {"claude": "claude"}
    saved = json.loads(
        brainstorm._actual_agents_path(tmp_path, TOPIC).read_text(encoding="utf-8")
    )
    assert saved == {"claude": "claude"}
    status = brainstorm.load_status_map(tmp_path, TOPIC)
    assert status["claude"].status == "succeeded"
    assert "codex" not in status
    assert "grok" not in status
    log = _commit_subjects(tmp_path)
    assert 'brainstorm: claude independent research on "my topic"' in log
    assert "codex independent research" not in log
    assert "grok independent research" not in log


def test_cancel_during_the_first_pass_zero_model_returns_an_empty_map(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    called: list[str] = []

    def run_fn(command, prompt, **kwargs):
        called.append(kwargs.get("agent_name") or command[0])
        raise agents.AgentCancelled("claude cancelled by user")

    result = brainstorm.run_pass_zero(
        tmp_path, MODELS, TOPIC, settings=settings,
        run_fn=run_fn, print_fn=lambda *a, **k: None,
    )
    assert called == ["claude"]
    assert result == {}
    assert brainstorm.load_status_map(tmp_path, TOPIC) == {}
    log = _commit_subjects(tmp_path)
    assert "independent research" not in log


def test_cancel_during_a_review_model_returns_the_accumulated_map(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_research(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex"), ("grok", "Grok")]
    prior = {"claude": "claude", "codex": "codex", "grok": "grok"}
    called: list[str] = []

    def run_fn(command, prompt, **kwargs):
        agent = kwargs.get("agent_name") or command[0]
        called.append(agent)
        shared = brainstorm.shared_path(tmp_path, TOPIC)
        text = shared.read_text(encoding="utf-8")
        if agent == "codex":
            shared.write_text(text + "\ncancelled mid-review\n", encoding="utf-8")
            raise agents.AgentCancelled("codex cancelled by user")
        shared.write_text(
            text.replace("claude research", "claude revised", 1), encoding="utf-8"
        )
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    result = brainstorm.run_review_pass(
        tmp_path, models, TOPIC, 1, settings=settings,
        run_fn=run_fn, print_fn=lambda *a, **k: None, actual_agents=dict(prior),
    )
    assert called == ["claude", "codex"]
    assert result == prior
    saved = json.loads(
        brainstorm._actual_agents_path(tmp_path, TOPIC).read_text(encoding="utf-8")
    )
    assert saved == prior
    log = _commit_subjects(tmp_path)
    assert 'brainstorm: claude review pass 1 on "my topic"' in log
    assert "codex review pass" not in log
    assert "grok review pass" not in log
    committed = subprocess.run(
        ["git", "show", "HEAD:docs/brainstorm/my-topic.md"],
        cwd=tmp_path, check=True, capture_output=True, text=True,
    ).stdout
    assert "claude revised" in committed
    assert "cancelled mid-review" not in committed


def test_cancel_during_synthesis_still_raises(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_research(tmp_path)
    called: list[str] = []

    def run_fn(command, prompt, **kwargs):
        called.append(kwargs.get("agent_name") or command[0])
        raise agents.AgentCancelled("claude cancelled by user")

    with pytest.raises(agents.AgentCancelled, match="cancelled by user"):
        brainstorm.run_final_synthesis(
            tmp_path, "claude", MODELS, TOPIC, settings=settings,
            run_fn=run_fn, print_fn=lambda *a, **k: None,
        )
    assert called == ["claude"]


def test_cancel_before_the_next_attempt_leaves_the_first_commit(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_research(tmp_path)
    cancel = threading.Event()
    called: list[str] = []

    def run_fn(command, prompt, **kwargs):
        called.append(kwargs.get("agent_name") or command[0])
        draft = brainstorm.plan_draft_path(tmp_path)
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("not a plan\n", encoding="utf-8")
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    def parsing(text):
        cancel.set()
        raise brainstorm.plan.PlanError("not a plan")

    monkeypatch.setattr(brainstorm.plan, "parse", parsing)
    with pytest.raises(agents.AgentCancelled, match="cancelled by user"):
        brainstorm.generate_plan_from_synthesis(
            tmp_path, settings, "claude", MODELS, TOPIC,
            run_fn=run_fn, cancel_event=cancel,
        )
    assert called == ["claude"]
    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    ).stdout
    assert 'brainstorm: claude drafts plan.md from synthesis on "my topic"' in log


def test_brainstorm_status_line_prints_no_limit(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    chat.save_default_agent(tmp_path, "claude")
    seen: dict = {}

    def fake_setup(root, settings, **kwargs):
        return {
            "topic": TOPIC,
            "models": MODELS,
            "passes": 0,
            "final_agent": "claude",
            "timeout_seconds": chat.NO_LIMIT,
        }

    def fake_pass_zero(root, models, topic, **kwargs):
        seen["timeout_seconds"] = kwargs.get("timeout_seconds", "MISSING")
        return {}

    monkeypatch.setattr(brainstorm, "ask_brainstorm_setup", fake_setup)
    monkeypatch.setattr(brainstorm, "run_pass_zero", fake_pass_zero)
    printed: list[str] = []
    answers = iter(["/brainstorm", "/exit"])
    chat.repl(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        which=lambda name: "/bin/x",
    )
    assert "Per-agent timeout: no limit." in printed
    assert seen["timeout_seconds"] is chat.NO_LIMIT
    assert not any(
        line.startswith("Per-agent timeout:") and "minutes" in line for line in printed
    )


def test_plan_source_forwards_no_limit_into_pass_zero(tmp_path: Path, monkeypatch):
    from whyline_relay import setup

    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    seen: dict = {}

    def fake_setup(*args, **kwargs):
        return {
            "topic": TOPIC,
            "models": MODELS,
            "passes": 1,
            "final_agent": "claude",
            "timeout_seconds": chat.NO_LIMIT,
        }

    def fake_pass_zero(root, models, topic, **kwargs):
        seen["timeout_seconds"] = kwargs.get("timeout_seconds", "MISSING")
        return {}

    monkeypatch.setattr(brainstorm, "ask_brainstorm_setup", fake_setup)
    monkeypatch.setattr(brainstorm, "run_pass_zero", fake_pass_zero)
    setup._run_brainstorm_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": "",
        print_fn=lambda *a, **k: None,
    )
    assert seen["timeout_seconds"] is chat.NO_LIMIT
