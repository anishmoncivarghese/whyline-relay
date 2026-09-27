from pathlib import Path

from whyline_relay import chat, chatlog


def _repo(tmp_path: Path) -> Path:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _fake_run_fn(command, prompt, **kwargs):
    from whyline_relay.agents import RunResult

    return RunResult(0, '{"type":"result","result":"an answer"}\n')


def test_repl_runs_setup_once_then_routes_to_the_default_agent(tmp_path: Path):
    root = _repo(tmp_path)
    which = lambda name: "/bin/claude" if name == "claude" else None
    lines = iter(["hello there", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=_fake_run_fn,
        which=which,
        setup_answers=iter(["claude"]),
    )
    assert any("an answer" in line for line in printed)
    assert chatlog.load(root)[0]["agent"] == "claude"


def test_repl_prefix_targets_a_specific_agent_without_changing_the_default(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    lines = iter(["/codex what changed", "/exit"])
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: None,
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert chatlog.load(root)[0]["agent"] == "codex"
    assert chat.load_default_agent(root) == "claude"


def test_repl_default_command_changes_the_saved_default(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    lines = iter(["/default codex", "/exit"])
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: None,
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert chat.load_default_agent(root) == "codex"


def test_repl_continues_after_a_missing_agent(tmp_path: Path):
    from whyline_relay import agents

    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")

    def raise_missing(command, prompt, **kwargs):
        raise agents.AgentMissing("claude is not installed or not on PATH")

    lines = iter(["hello", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=raise_missing,
        which=lambda name: "/bin/x",
    )
    assert any("not installed" in line for line in printed)
    assert chatlog.load(root) == []


def test_repl_continues_after_a_timed_out_turn(tmp_path: Path):
    from whyline_relay import agents

    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")

    def raise_timeout(command, prompt, **kwargs):
        raise agents.AgentTimeout("claude exceeded 300s and was terminated")

    lines = iter(["hello", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=raise_timeout,
        which=lambda name: "/bin/x",
    )
    assert any("try again" in line.lower() for line in printed)


def test_repl_flags_a_rate_limited_turn(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")

    def fake_rate_limited(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult

        return RunResult(1, "You have exceeded your usage limit. Try again later.\n")

    lines = iter(["hello", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=fake_rate_limited,
        which=lambda name: "/bin/x",
    )
    assert any("rate-limited" in line for line in printed)


def test_repl_unknown_slash_command_is_rejected_without_a_turn(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    lines = iter(["/notacommand", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert chatlog.load(root) == []
    assert any("Unknown command" in line for line in printed)


def test_repl_history_command_replays_the_transcript(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    chatlog.append(root, agent="claude", prompt="q", response="a", files_changed=0, ok=True)
    lines = iter(["/history", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert any("q" in line for line in printed)


def test_repl_clear_wipes_history_after_confirmation(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    chatlog.append(root, agent="claude", prompt="q", response="a", files_changed=0, ok=True)
    lines = iter(["/clear", "y", "/exit"])
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: None,
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert chatlog.load(root) == []
