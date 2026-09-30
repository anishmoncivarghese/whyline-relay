import subprocess
from pathlib import Path

from whyline_relay import brainstorm, chat, config
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

    pass_zero: list[int] = []
    brainstorm.run_pass_zero(
        tmp_path, MODELS, TOPIC, settings=settings,
        run_fn=_recording_run(pass_zero), print_fn=lambda *a, **k: None,
    )
    assert pass_zero == [chat.CHAT_TIMEOUT_SECONDS, chat.CHAT_TIMEOUT_SECONDS]

    review: list[int] = []
    brainstorm.run_review_pass(
        tmp_path, MODELS, TOPIC, 1, settings=settings,
        run_fn=_recording_run(review), print_fn=lambda *a, **k: None,
    )
    assert review == [chat.CHAT_TIMEOUT_SECONDS, chat.CHAT_TIMEOUT_SECONDS]

    synthesis: list[int] = []
    brainstorm.run_final_synthesis(
        tmp_path, "claude", MODELS, TOPIC, settings=settings,
        run_fn=_recording_run(synthesis), print_fn=lambda *a, **k: None,
    )
    assert synthesis == [chat.CHAT_TIMEOUT_SECONDS]

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
    assert draft == [chat.CHAT_TIMEOUT_SECONDS]
