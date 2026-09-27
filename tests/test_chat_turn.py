import json
import subprocess
from pathlib import Path

import pytest

from whyline_relay import chat, chatlog, config


def _deliver(command: list[str], text: str) -> None:
    """Write `text` to a chat `-o` path when the command has one.

    Codex's adapter sets uses_output_file, so run_turn reads the response
    from that file and ignores RunResult.output. The file is the last
    message as plain text (extract_response strips it); it is not Claude's
    JSON envelope. A fake that only returns RunResult leaves the backup's
    raw text empty, so a rate limit on the backup is invisible and the
    login-status check can run for real.
    """
    if "-o" in command:
        Path(command[command.index("-o") + 1]).write_text(text, encoding="utf-8")


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def test_resolve_command_works_for_claude_with_no_config_file(tmp_path: Path):
    settings = config.load(tmp_path)
    command = chat.resolve_command(settings, "claude")
    assert command[0] == "claude"


def test_resolve_command_refuses_an_unconfigured_generic_agent(tmp_path: Path):
    settings = config.load(tmp_path)
    with pytest.raises(chat.AgentUnavailable):
        chat.resolve_command(settings, "grok")


def test_resolve_command_works_for_a_configured_generic_agent(tmp_path: Path):
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        '[agents.grok]\nadapter = "generic"\ncommand = ["grok", "-p"]\n'
    )
    settings = config.load(tmp_path)
    assert chat.resolve_command(settings, "grok") == ["grok", "-p"]


def test_run_turn_captures_the_response_and_logs_it(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(0, '{"type":"result","result":"pong"}\n')

    record = chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings, run_fn=fake_run_fn
    )
    assert record["response"] == "pong"
    assert record["agent"] == "claude"
    assert record["ok"] is True
    turns = chatlog.load(tmp_path)
    assert len(turns) == 1
    assert turns[0]["response"] == "pong"


def test_run_turn_commits_when_the_agent_leaves_the_tree_dirty(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        (tmp_path / "new.txt").write_text("made by the agent\n")
        return RunResult(0, '{"type":"result","result":"done"}\n')

    record = chat.run_turn(
        tmp_path, agent="claude", prompt="add a file", settings=settings, run_fn=fake_run_fn
    )
    assert record["files_changed"] == 1
    assert "new.txt" in record["diff_stat"]
    persisted = chatlog.load(tmp_path)[-1]
    assert record["timestamp"] == persisted["timestamp"]
    assert "diff_stat" not in persisted
    assert "rate_limited" not in persisted
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout
    assert "chat: claude turn" in log


def test_run_turn_does_not_commit_when_the_tree_is_clean(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(0, '{"type":"result","result":"just an answer"}\n')

    record = chat.run_turn(
        tmp_path, agent="claude", prompt="what is this", settings=settings, run_fn=fake_run_fn
    )
    assert record["files_changed"] == 0
    assert "diff_stat" not in record


def test_run_turn_marks_a_reported_failure_but_still_logs_it(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(1, "agent crashed\n")

    record = chat.run_turn(
        tmp_path, agent="claude", prompt="do it", settings=settings, run_fn=fake_run_fn
    )
    assert record["ok"] is False


def test_run_turn_flags_a_rate_limited_response(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(1, "You have exceeded your usage limit. Try again later.\n")

    record = chat.run_turn(
        tmp_path, agent="claude", prompt="do it", settings=settings, run_fn=fake_run_fn
    )
    assert record["rate_limited"] is True


def test_run_turn_does_not_flag_cancelled_grok_reasoning_as_rate_limited(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        '[agents.grok]\nadapter = "generic"\ncommand = ["grok", "-p"]\n'
    )
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult

        return RunResult(
            0,
            json.dumps(
                {
                    "text": "I will inspect the repository first.",
                    "stopReason": "cancelled",
                    "thought": "The task discusses a usage limit.",
                },
                indent=2,
            ),
        )

    record = chat.run_turn(
        tmp_path,
        agent="grok",
        prompt="document failover",
        settings=settings,
        run_fn=fake_run_fn,
    )
    assert record["response"] == "I will inspect the repository first."
    assert record["ok"] is False
    assert record["rate_limited"] is False


def test_run_turn_includes_recent_history_in_the_prompt(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    chatlog.append(
        tmp_path, agent="codex", prompt="earlier question",
        response="earlier answer", files_changed=0, ok=True,
    )
    seen_prompts = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        seen_prompts.append(prompt)
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    chat.run_turn(
        tmp_path, agent="claude", prompt="new question", settings=settings, run_fn=fake_run_fn
    )
    assert "earlier question" in seen_prompts[0]
    assert "new question" in seen_prompts[0]


def test_run_turn_returns_the_persisted_chat_record(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(0, '{"type":"result","result":"pong"}\n')

    record = chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings, run_fn=fake_run_fn
    )
    persisted = chatlog.load(tmp_path)[-1]
    assert set(persisted) == {
        "agent", "prompt", "response", "timestamp", "files_changed", "ok",
    }
    assert {key: record[key] for key in persisted} == persisted
    assert record["rate_limited"] is False
    assert "diff_stat" not in record


def test_run_turn_generates_claude_settings_when_init_never_ran(tmp_path: Path):
    # Regression proof: claude's own managed default_command references
    # .whyline/relay/claude-settings.json, which only `whyline-relay init`
    # used to create -- so a repo that only ever ran `chat` (no init) would
    # make the real claude CLI fail with "Settings file not found." Verified
    # empirically against the real claude binary before this test was added.
    _init_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(0, '{"type":"result","result":"pong"}\n')

    chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings, run_fn=fake_run_fn
    )
    settings_path = tmp_path / ".whyline" / "relay" / "claude-settings.json"
    assert settings_path.exists()
    assert "Bash(git commit:*)" in settings_path.read_text()


def test_run_turn_never_overwrites_a_customized_claude_settings_file(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    settings_path = tmp_path / ".whyline" / "relay" / "claude-settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text('{"custom": true}')

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(0, '{"type":"result","result":"pong"}\n')

    chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings, run_fn=fake_run_fn
    )
    assert settings_path.read_text() == '{"custom": true}'


def test_run_turn_does_not_generate_permission_files_for_generic_agents(tmp_path: Path):
    _init_repo(tmp_path)
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        '[agents.grok]\nadapter = "generic"\ncommand = ["grok", "-p"]\n'
    )
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(0, '{"text":"pong"}\n')

    chat.run_turn(
        tmp_path, agent="grok", prompt="ping", settings=settings, run_fn=fake_run_fn
    )
    assert not (relay / "claude-settings.json").exists()


def test_run_turn_switches_to_the_backup_and_retries_automatically(tmp_path: Path):
    _init_repo(tmp_path)
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "config.toml").write_text('[chat.backup]\nclaude = "codex"\n')
    settings = config.load(tmp_path)
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        calls.append(command[0])
        if command[0] == "claude":
            return RunResult(1, "You have exceeded your usage limit. Try again later.")
        text = "pong from backup\n"
        _deliver(command, text)
        return RunResult(0, text)
    # codex's own "pong from backup" response has no rate-limit marker, so
    # the post-retry failover_reason check falls through to a login-status
    # re-check -- fake the subprocess runner so this never shells out for
    # real. "still logged in" is returncode 0, matching still_logged_in()'s
    # own check.
    fake_login_ok = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    record = chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings,
        run_fn=fake_run_fn, runner=fake_login_ok,
    )
    assert calls == ["claude", "codex"]
    assert record["agent"] == "codex"
    assert record["response"] == "pong from backup"
    assert "claude hit a usage or rate limit" in record["failover_notice"]
    from whyline_relay import failover
    override = failover.read_overrides(tmp_path, failover.chat_path(tmp_path))
    assert override["claude"].agent == "codex"
    assert override["claude"].reason == "rate-limit"


def test_run_turn_reports_and_stops_when_the_backup_also_fails(tmp_path: Path):
    _init_repo(tmp_path)
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "config.toml").write_text('[chat.backup]\nclaude = "codex"\n')
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        text = "You have exceeded your usage limit. Try again later."
        _deliver(command, text)
        return RunResult(1, text)

    record = chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings, run_fn=fake_run_fn
    )
    assert record["agent"] == "codex"
    assert "also" in record["failover_notice"]
    assert "hit a usage or rate limit" in record["failover_notice"]


def test_run_turn_uses_an_already_active_backup_silently(tmp_path: Path):
    _init_repo(tmp_path)
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "config.toml").write_text('[chat.backup]\nclaude = "codex"\n')
    settings = config.load(tmp_path)
    from whyline_relay import failover
    failover.write_override(
        tmp_path, "claude",
        failover.ActiveOverride("codex", "claude", "rate-limit", "2026-01-01T00:00:00"),
        storage_path=failover.chat_path(tmp_path),
    )
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        calls.append(command[0])
        text = "pong\n"
        _deliver(command, text)
        return RunResult(0, text)
    # codex's response has no rate-limit marker, so the already-on-a-backup
    # branch's failover_reason check falls through to a login-status
    # re-check -- fake the runner so this never shells out for real.
    fake_login_ok = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    record = chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings,
        run_fn=fake_run_fn, runner=fake_login_ok,
    )
    assert calls == ["codex"]
    assert record["agent"] == "codex"
    assert "failover_notice" not in record


def test_run_turn_with_no_backup_configured_behaves_exactly_as_before(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(1, "You have exceeded your usage limit. Try again later.")

    record = chat.run_turn(
        tmp_path, agent="claude", prompt="ping", settings=settings, run_fn=fake_run_fn
    )
    assert record["agent"] == "claude"
    assert record["rate_limited"] is True
    assert "failover_notice" not in record
