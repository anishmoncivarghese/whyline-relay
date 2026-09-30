from pathlib import Path

from whyline_relay import agents, chat, chatlog
from whyline_relay.agents import RunResult


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


def test_repl_backups_command_reports_none_active(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    lines = iter(["/backups", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert any("no active" in line.lower() for line in printed)


def test_repl_backups_command_lists_an_active_override(tmp_path: Path):
    from whyline_relay import failover

    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    failover.write_override(
        root, "claude",
        failover.ActiveOverride("codex", "claude", "rate-limit", "2026-01-01T00:00:00"),
        storage_path=failover.chat_path(root),
    )
    lines = iter(["/backups", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert any("claude" in line and "codex" in line for line in printed)


def test_repl_reset_backup_clears_one_agent(tmp_path: Path):
    from whyline_relay import failover

    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    failover.write_override(
        root, "claude",
        failover.ActiveOverride("codex", "claude", "rate-limit", "t"),
        storage_path=failover.chat_path(root),
    )
    lines = iter(["/reset-backup claude", "/backups", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert failover.read_overrides(root, failover.chat_path(root)) == {}
    assert any("no active" in line.lower() for line in printed)


def test_repl_reset_backup_with_no_argument_clears_all(tmp_path: Path):
    from whyline_relay import failover

    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    failover.write_override(
        root, "claude",
        failover.ActiveOverride("codex", "claude", "rate-limit", "t"),
        storage_path=failover.chat_path(root),
    )
    failover.write_override(
        root, "codex",
        failover.ActiveOverride("grok", "codex", "auth", "t"),
        storage_path=failover.chat_path(root),
    )
    lines = iter(["/reset-backup", "/exit"])
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: None,
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    assert failover.read_overrides(root, failover.chat_path(root)) == {}


def test_repl_prints_the_failover_notice_when_present(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    monkeypatch.setattr(
        chat,
        "run_turn",
        lambda *a, **k: {
            "agent": "codex",
            "prompt": "hello",
            "response": "pong",
            "ok": True,
            "rate_limited": False,
            "failover_notice": "claude hit a usage limit; trying its backup, codex...",
        },
    )
    lines = iter(["hello", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        which=lambda name: "/bin/x",
    )
    assert any("trying its backup" in line for line in printed)


def test_repl_brainstorm_runs_the_whole_flow(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    from whyline_relay import brainstorm

    def brainstorm_run_fn(command, prompt, **kwargs):
        if "independently" in prompt:
            for agent in ("claude", "codex"):
                path = brainstorm.temp_path(root, agent)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"research from {agent}\n", encoding="utf-8")
        return _fake_run_fn(command, prompt, **kwargs)

    answers = iter([
        "/brainstorm",
        "caching strategy",  # topic
        "1,2",  # models: claude, codex
        "0",  # passes
        "claude",  # final synthesis model
        "",  # timeout default (15m)
        "/exit",
    ])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=brainstorm_run_fn,
        which=lambda name: "/bin/x",
    )
    assert any("Per-agent timeout: 15 minutes." in line for line in printed)
    assert any("an answer" in line for line in printed)
    shared = brainstorm.shared_path(root, "caching strategy")
    assert shared.exists()


def test_repl_brainstorm_declined_after_unavailable_model_does_nothing(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    answers = iter([
        "/brainstorm",
        "some topic",
        "4",  # grok -- not configured for chat in this repo
        "0",
        "grok",
        "",  # timeout default (15m)
        "n",  # decline to proceed without it
        "/exit",
    ])
    chat.repl(
        root,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
        run_fn=_fake_run_fn,
        which=lambda name: "/bin/x",
    )
    from whyline_relay import brainstorm
    assert not brainstorm.shared_path(root, "some topic").exists()


def test_repl_brainstorm_renders_compact_progress_table_on_success(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    from whyline_relay import brainstorm

    def brainstorm_run_fn(command, prompt, **kwargs):
        if "independently" in prompt:
            for agent in ("claude", "codex"):
                path = brainstorm.temp_path(root, agent)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"research from {agent}\n", encoding="utf-8")
        return _fake_run_fn(command, prompt, **kwargs)

    answers = iter([
        "/brainstorm",
        "api architecture",
        "1,2",  # claude, codex
        "1",    # 1 review pass
        "claude",
        "",     # timeout default (15m)
        "/exit",
    ])
    printed: list[str] = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=brainstorm_run_fn,
        which=lambda name: "/bin/x",
    )

    # 1. Output contains the table headers
    assert any("Per-agent timeout: 15 minutes." in line for line in printed)
    assert any("Agent" in line and "Phase" in line and "State" in line for line in printed)
    assert any("Elapsed Time" in line and "Failure Reason" in line for line in printed)

    # 2. Output contains rows for pass-zero, review, and final synthesis
    table_output = "\n".join(printed)
    assert "Claude" in table_output
    assert "Codex" in table_output
    assert "pass-zero" in table_output
    assert "review pass 1" in table_output
    assert "final synthesis" in table_output
    assert "succeeded" in table_output

    # 3. Final answer is also printed
    assert any("an answer" in line for line in printed)


def test_repl_brainstorm_renders_progress_table_on_pass_zero_failure(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")

    def failing_run_fn(command, prompt, **kwargs):
        return RunResult(1, "quota exceeded: rate limit reached\n")

    answers = iter([
        "/brainstorm",
        "failing topic",
        "1,2",  # claude, codex
        "0",
        "claude",
        "",     # timeout default (15m)
        "/exit",
    ])
    printed: list[str] = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=failing_run_fn,
        which=lambda name: "/bin/x",
    )

    # 1. Progress table is rendered despite failure
    table_output = "\n".join(printed)
    assert "Agent" in table_output and "Failure Reason" in table_output
    assert "Claude" in table_output
    assert "Codex" in table_output
    assert "failed" in table_output
    assert "quota/rate-limit" in table_output

    # 2. Actionable stopping message is printed
    assert any("No selected agent succeeded; stopping brainstorm without synthesis" in line for line in printed)


def test_repl_brainstorm_renders_progress_table_on_synthesis_failure(tmp_path: Path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    from whyline_relay import brainstorm

    def synth_fail_run_fn(command, prompt, **kwargs):
        if "Final Synthesis" in prompt:
            raise agents.AgentTimeout("synthesis timed out")
        if "independently" in prompt:
            for agent in ("claude", "codex"):
                path = brainstorm.temp_path(root, agent)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"research from {agent}\n", encoding="utf-8")
            return _fake_run_fn(command, prompt, **kwargs)
        return _fake_run_fn(command, prompt, **kwargs)

    answers = iter([
        "/brainstorm",
        "synth fail topic",
        "1",  # claude only
        "0",
        "claude",
        "",   # timeout default (15m)
        "/exit",
    ])
    printed: list[str] = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=synth_fail_run_fn,
        which=lambda name: "/bin/x",
    )

    # 1. Progress table rendered with pass-zero success and synthesis failure
    table_output = "\n".join(printed)
    assert "Agent" in table_output and "Failure Reason" in table_output
    assert "pass-zero" in table_output
    assert "final synthesis" in table_output
    assert "timeout" in table_output

    # 2. Error message printed
    assert any("timed out" in line.lower() for line in printed)
