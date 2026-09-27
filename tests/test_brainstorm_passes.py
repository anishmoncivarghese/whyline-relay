import subprocess
from pathlib import Path

from whyline_relay import brainstorm, config


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def test_run_review_pass_invokes_every_model_once(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        calls.append(command[0])
        return RunResult(0, '{"type":"result","result":"revised"}\n')

    brainstorm.run_review_pass(
        tmp_path, models, "my topic", 1, settings=settings, run_fn=fake_run_fn,
        print_fn=lambda *a, **k: None,
    )
    assert calls == ["claude", "codex"]


def test_run_review_pass_skips_a_model_that_times_out(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay import agents
        from whyline_relay.agents import RunResult
        calls.append(command[0])
        if command[0] == "claude":
            raise agents.AgentTimeout("claude exceeded 300s")
        return RunResult(0, '{"type":"result","result":"revised"}\n')

    printed = []
    brainstorm.run_review_pass(
        tmp_path, models, "my topic", 1, settings=settings, run_fn=fake_run_fn,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    assert calls == ["claude", "codex"]  # codex still ran despite claude's timeout
    assert any("claude" in line for line in printed)


def test_run_review_pass_commit_message_names_the_pass_and_topic(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude")]
    seen = {}

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(0, '{"type":"result","result":"revised"}\n')

    real_run_turn = brainstorm.chat.run_turn

    def spying_run_turn(*args, **kwargs):
        seen["commit_message"] = kwargs.get("commit_message")
        return real_run_turn(*args, **kwargs)

    monkeypatch.setattr(brainstorm.chat, "run_turn", spying_run_turn)
    brainstorm.run_review_pass(
        tmp_path, models, "my topic", 2, settings=settings,
        run_fn=fake_run_fn, print_fn=lambda *a, **k: None,
    )
    assert seen["commit_message"] == 'brainstorm: claude review pass 2 on "my topic"'


def test_run_final_synthesis_only_invokes_the_designated_model(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        calls.append(command[0])
        # Codex's adapter sets uses_output_file, so run_turn reads the
        # response from `-o` and ignores RunResult.output. The file is the
        # last message as plain text; Claude's JSON envelope would come
        # back unparsed.
        text = "final answer\n"
        if "-o" in command:
            Path(command[command.index("-o") + 1]).write_text(text, encoding="utf-8")
        return RunResult(0, text)

    record = brainstorm.run_final_synthesis(
        tmp_path, "codex", models, "my topic", settings=settings, run_fn=fake_run_fn
    )
    assert calls == ["codex"]
    assert record["response"] == "final answer"
    assert record["agent"] == "codex"
