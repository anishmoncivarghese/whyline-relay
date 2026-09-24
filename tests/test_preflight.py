import os
import subprocess
import sys
from pathlib import Path

import pytest

from whyline_relay import cli, preflight, prompts, state


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


@pytest.fixture
def ready_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        f'plan = "plan.md"\n'
        f'[agents.codex]\ncommand = ["{sys.executable}", "codex-role"]\n'
        f'[agents.claude]\ncommand = ["{sys.executable}", "claude-role"]\n'
    )
    (tmp_path / "plan.md").write_text("- [ ] APP-1: Build it\n  Include tests.\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "ready")
    return tmp_path


def successful_runner(calls: list[list[str]] | None = None):
    def run(argv, **kwargs):
        if calls is not None:
            calls.append(argv)
        return _completed(argv)

    return run


PIPELINE_TOML = '''
[roles]
implementer = "codex"
tester = "claude"
reviewer = "claude"
[pipeline]
default_profile = "full"
[pipeline.profiles]
full = ["draft", "test", "review"]
[pipeline.stages.draft]
role = "implementer"
prompt = "implement"
[pipeline.stages.draft.on]
ready = "@next"
[pipeline.stages.test]
role = "tester"
prompt = "test"
[pipeline.stages.test.on]
passed = "@next"
failed = "draft"
[pipeline.stages.review]
role = "reviewer"
prompt = "review"
[pipeline.stages.review.on]
approved = "@complete"
rejected = "draft"
'''


def _pipeline_repo(tmp_path: Path, extra_toml: str = "") -> Path:
    """A git repo with a real [pipeline] config -- no override for the "test"
    stage's prompt, so it has no built-in template and no override file: this
    is the unresolvable-prompt case Task 5's PromptError exists for."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    codex_cmd = f'["{sys.executable}", "codex-role"]'
    claude_cmd = f'["{sys.executable}", "claude-role"]'
    (relay / "config.toml").write_text(
        PIPELINE_TOML
        + f'\n[agents.codex]\ncommand = {codex_cmd}\n'
        + f'[agents.claude]\ncommand = {claude_cmd}\n'
        + extra_toml
    )
    (tmp_path / "plan.md").write_text("- [ ] T-1: build it\n  Include tests.\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "setup")
    return tmp_path


def test_a_bypass_flag_on_a_pipeline_agent_is_caught(tmp_path):
    # Regression proof for the _agents_in_use fix: before it, doctor's bypass
    # check only ever looked at settings.roles.implementer/.reviewer, which are
    # meaningless placeholders once [pipeline] is configured -- a pipeline's
    # real agents (here, "claude", filling both "tester" and "reviewer") went
    # completely unchecked.
    root = _pipeline_repo(tmp_path)
    config_path = root / ".whyline" / "relay" / "config.toml"
    config_path.write_text(
        config_path.read_text().replace(
            f'command = ["{sys.executable}", "claude-role"]',
            f'command = ["{sys.executable}", "claude-role", "--dangerously-skip-permissions"]',
        )
    )
    checks = preflight.run(root, runner=successful_runner())
    assert any(c.status == "FAIL" and "permission-bypass" in c.message for c in checks)


def test_a_stage_naming_an_unresolvable_prompt_fails(tmp_path):
    # "test" is now a built-in template (it ships real tester content), so this
    # names a stage prompt that is neither built in nor overridden by anyone --
    # a fresh, self-contained [pipeline] rather than PIPELINE_TOML, since a
    # stage's prompt can't be overridden by concatenating extra_toml (TOML
    # rejects a second definition of a table PIPELINE_TOML already has).
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        f'[agents.codex]\ncommand = ["{sys.executable}", "codex-role"]\n'
        f'[agents.claude]\ncommand = ["{sys.executable}", "claude-role"]\n'
        "[roles]\n"
        'implementer = "codex"\n'
        "[pipeline]\n"
        'default_profile = "solo"\n'
        "[pipeline.profiles]\n"
        'solo = ["only"]\n'
        "[pipeline.stages.only]\n"
        'role = "implementer"\n'
        'prompt = "docgen"\n'
        "[pipeline.stages.only.on]\n"
        'ready = "@complete"\n'
    )
    (tmp_path / "plan.md").write_text("- [ ] T-1: build it\n  Include tests.\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "setup")

    checks = preflight.run(tmp_path, runner=successful_runner())
    assert any(
        c.status == "FAIL" and "no prompt named 'docgen'" in c.message
        for c in checks
    )


def test_a_task_naming_an_unknown_relay_profile_fails(tmp_path):
    root = _pipeline_repo(tmp_path)
    prompt_dir = root / ".whyline" / "relay" / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "test.md").write_text("Test {task_id} as {actor}.")
    (root / "plan.md").write_text("- [ ] T-1: x\n  relay-profile: nonexistent\n")
    checks = preflight.run(root, root / "plan.md", runner=successful_runner())
    assert any(
        c.status == "FAIL" and "relay-profile" in c.message and "nonexistent" in c.message
        for c in checks
    )


def test_a_task_naming_relay_profile_with_no_pipeline_configured_warns(ready_repo: Path):
    (ready_repo / "plan.md").write_text("- [ ] T-1: x\n  relay-profile: full\n")
    checks = preflight.run(ready_repo, runner=successful_runner())
    assert any(c.status == "warn" and "relay-profile" in c.message for c in checks)


def test_all_checks_pass_in_documented_order(ready_repo: Path):
    checks = preflight.run(ready_repo, runner=successful_runner())

    assert [(check.status, check.message) for check in checks] == [
        ("ok", "directory is inside a git repository"),
        ("ok", "whyline is installed and initialised"),
        ("ok", "relay setup is complete"),
        ("ok", f"{sys.executable} is on PATH"),
        ("ok", f"plan parses and has unchecked tasks: {ready_repo / 'plan.md'}"),
        ("ok", "working tree is clean"),
        ("ok", "no other relay is running here"),
    ]


def test_not_a_git_repository_fails_with_fix(tmp_path: Path):
    checks = preflight.run(tmp_path, allow_dirty=True, runner=successful_runner())
    assert checks[0] == preflight.Check(
        "FAIL",
        "directory is not inside a git repository",
        "run it inside a git repository",
    )


@pytest.mark.parametrize(
    ("runner", "message", "hint"),
    [
        (
            lambda argv, **kwargs: (_ for _ in ()).throw(FileNotFoundError("whyline")),
            "whyline is not installed",
            "install whyline: uv tool install whyline",
        ),
        (
            lambda argv, **kwargs: _completed(argv, 1, stderr="not initialised"),
            "whyline is not initialised: not initialised",
            "whyline init --yes",
        ),
    ],
)
def test_whyline_failures_have_specific_fixes(ready_repo, runner, message, hint):
    check = preflight.run(ready_repo, runner=runner)[1]
    assert check == preflight.Check("FAIL", message, hint)


def test_relay_setup_requires_config(ready_repo: Path):
    (ready_repo / ".whyline" / "relay" / "config.toml").unlink()
    check = preflight.run(ready_repo, allow_dirty=True, runner=successful_runner())[2]
    assert check.status == "FAIL"
    assert check.hint == "whyline-relay init"


def test_relay_setup_requires_configured_settings_file(ready_repo: Path):
    config_path = ready_repo / ".whyline" / "relay" / "config.toml"
    config_path.write_text(
        '[agents.codex]\ncommand = ["python", "run.py"]\n'
        '[agents.claude]\ncommand = ["python", "run.py", "--settings", "missing.json"]\n'
    )
    check = preflight.run(ready_repo, allow_dirty=True, runner=successful_runner())[2]
    assert check == preflight.Check(
        "FAIL", "settings file is missing: missing.json", "whyline-relay init"
    )


def test_every_distinct_configured_program_is_checked(ready_repo: Path, monkeypatch):
    target = ready_repo / ".whyline" / "relay" / "config.toml"
    target.write_text(
        '[agents.codex]\ncommand = ["present", "run"]\n'
        '[agents.claude]\ncommand = ["missing", "run"]\n'
    )
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: "/bin/present" if name == "present" else None,
    )
    checks = preflight.run(ready_repo, allow_dirty=True, runner=successful_runner())
    program_checks = checks[3:5]
    assert program_checks == [
        preflight.Check("ok", "present is on PATH"),
        preflight.Check(
            "FAIL",
            "missing is not on PATH",
            "install missing and make sure it is on PATH",
        ),
    ]


def test_missing_backup_program_names_its_role(ready_repo: Path, monkeypatch):
    target = ready_repo / ".whyline" / "relay" / "config.toml"
    target.write_text(
        '[roles.backup]\nimplementer = "aider"\n'
        f'[agents.codex]\ncommand = ["{sys.executable}", "codex-role"]\n'
        f'[agents.claude]\ncommand = ["{sys.executable}", "claude-role"]\n'
        '[agents.aider]\nadapter = "generic"\ncommand = ["aider", "--message"]\n'
    )
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: None if name == "aider" else name,
    )

    checks = preflight.run(
        ready_repo, allow_dirty=True, runner=successful_runner()
    )

    assert any(
        check
        == preflight.Check(
            "FAIL",
            "aider (backup for implementer) is not on PATH",
            "install aider and make sure it is on PATH",
        )
        for check in checks
    )


def test_codex_and_claude_logins_are_checked(ready_repo: Path, monkeypatch):
    target = ready_repo / ".whyline" / "relay" / "config.toml"
    target.write_text(
        '[agents.codex]\ncommand = ["codex", "exec"]\n'
        '[agents.claude]\ncommand = ["claude", "-p"]\n'
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/bin/{name}")
    calls = []

    checks = preflight.run(ready_repo, allow_dirty=True, runner=successful_runner(calls))

    assert calls == [
        ["whyline", "sync"],
        ["codex", "login", "status"],
        ["claude", "auth", "status"],
    ]
    assert [check.message for check in checks[5:7]] == [
        "codex is logged in",
        "claude is logged in",
    ]


def test_failed_login_blocks_and_unrunnable_or_slow_login_warns(ready_repo, monkeypatch):
    target = ready_repo / ".whyline" / "relay" / "config.toml"
    target.write_text(
        '[agents.codex]\ncommand = ["codex", "exec"]\n'
        '[agents.claude]\ncommand = ["claude", "-p"]\n'
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/bin/{name}")

    def runner(argv, **kwargs):
        if argv[0] == "whyline":
            return _completed(argv)
        if argv[0] == "codex":
            return _completed(argv, 1)
        raise subprocess.TimeoutExpired(argv, 20)

    checks = preflight.run(ready_repo, allow_dirty=True, runner=runner)
    login_checks = checks[5:7]
    assert login_checks[0] == preflight.Check(
        "FAIL", "codex is not logged in", "codex login"
    )
    assert login_checks[1].status == "warn"
    assert login_checks[1].hint == "claude auth login"
    assert preflight.failures(login_checks) == 1


def test_failed_backup_login_names_its_role(ready_repo: Path, monkeypatch):
    backup = ready_repo / "claude"
    backup.write_text("#!/bin/sh\nexit 0\n")
    backup.chmod(0o755)
    target = ready_repo / ".whyline" / "relay" / "config.toml"
    target.write_text(
        '[roles]\nreviewer = "codex"\n'
        '[roles.backup]\nimplementer = "claude"\n'
        f'[agents.codex]\ncommand = ["{sys.executable}", "codex-role"]\n'
        f'[agents.claude]\ncommand = ["{backup}", "-p"]\n'
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: name)

    def runner(argv, **kwargs):
        return _completed(argv, 0 if argv[0] == "whyline" else 1)

    checks = preflight.run(ready_repo, allow_dirty=True, runner=runner)

    assert any(
        check
        == preflight.Check(
            "FAIL",
            "claude (backup for implementer) is not logged in",
            "claude auth login",
        )
        for check in checks
    )


def test_the_summary_line_labels_a_backup_as_a_backup_not_a_primary(
    ready_repo: Path, monkeypatch
):
    """Regression: the per-role summary loop derived its label from `agent ==

    roles.implementer` alone, so a backup-only agent (never a primary) was
    described as if it were currently filling that role.
    """
    target = ready_repo / ".whyline" / "relay" / "config.toml"
    target.write_text(
        '[roles]\nimplementer = "claude"\nreviewer = "claude"\n'
        '[roles.backup]\nreviewer = "codex"\n'
        f'[agents.codex]\ncommand = ["{sys.executable}", "codex-role"]\n'
        f'[agents.claude]\ncommand = ["{sys.executable}", "claude-role"]\n'
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: name)

    checks = preflight.run(ready_repo, allow_dirty=True, runner=successful_runner())

    messages = [check.message for check in checks]
    assert not any(msg.startswith("reviewer: codex") for msg in messages), messages
    assert any(msg.startswith("reviewer backup: codex") for msg in messages), messages


def test_non_codex_or_claude_program_skips_login_check(ready_repo: Path):
    calls = []
    checks = preflight.run(ready_repo, runner=successful_runner(calls))
    assert calls == [["whyline", "sync"]]
    assert not any("logged in" in check.message for check in checks)


def test_only_agents_filling_roles_are_checked(ready_repo: Path, monkeypatch):
    target = ready_repo / ".whyline" / "relay" / "config.toml"
    target.write_text(
        '[roles]\nimplementer = "claude"\nreviewer = "claude"\n'
        '[agents.codex]\ncommand = ["codex", "--settings", "missing.json"]\n'
        '[agents.claude]\ncommand = ["claude", "-p"]\n'
    )
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: "/bin/claude" if name == "claude" else None,
    )

    checks = preflight.run(
        ready_repo, allow_dirty=True, runner=successful_runner()
    )

    assert checks[2] == preflight.Check("ok", "relay setup is complete")
    assert not any(check.message == "codex is not on PATH" for check in checks)
    assert any(
        check
        == preflight.Check(
            "warn",
            "claude is both the implementer and the reviewer, so the review is not independent",
        )
        for check in checks
    )


def test_generic_agent_is_checked_and_warned_without_login(
    ready_repo: Path, monkeypatch
):
    target = ready_repo / ".whyline" / "relay" / "config.toml"
    target.write_text(
        '[roles]\nreviewer = "aider"\n'
        f'[agents.codex]\ncommand = ["{sys.executable}", "codex-role"]\n'
        '[agents.aider]\nadapter = "generic"\ncommand = ["aider", "--message"]\n'
    )
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: f"/bin/{name}" if name == "aider" else name,
    )
    calls: list[list[str]] = []

    checks = preflight.run(
        ready_repo, allow_dirty=True, runner=successful_runner(calls)
    )

    assert any(check.message == "aider is on PATH" for check in checks)
    assert [check for check in checks if "aider is a generic agent" in check.message] == [
        preflight.Check(
            "warn",
            "aider is a generic agent: the relay does not manage its permissions, login or denials",
        )
    ]
    assert calls == [["whyline", "sync"]]


@pytest.mark.parametrize("template", ["stale", "fresh", "missing"])
def test_non_default_roles_validate_existing_prompt_templates(
    ready_repo: Path, template: str
):
    target = ready_repo / ".whyline" / "relay" / "config.toml"
    target.write_text(
        '[roles]\nimplementer = "claude"\nreviewer = "codex"\n'
        f'[agents.codex]\ncommand = ["{sys.executable}", "codex-role"]\n'
        f'[agents.claude]\ncommand = ["{sys.executable}", "claude-role"]\n'
    )
    prompt = ready_repo / ".whyline" / "relay" / "prompts" / "implement.md"
    if template != "missing":
        prompt.parent.mkdir()
        content = prompts.IMPLEMENT
        if template == "stale":
            content = content.replace("{implementer}", "codex").replace(
                "{reviewer}", "claude"
            )
        prompt.write_text(content)

    checks = preflight.run(
        ready_repo, allow_dirty=True, runner=successful_runner()
    )
    template_failures = [
        check
        for check in checks
        if check.message.startswith("prompt template .whyline/relay/prompts/")
    ]

    if template == "stale":
        assert template_failures == [
            preflight.Check(
                "FAIL",
                "prompt template .whyline/relay/prompts/implement.md does not use "
                "{implementer} and {reviewer}, so an agent would be told the wrong names",
                "whyline-relay init --overwrite",
            )
        ]
    else:
        assert template_failures == []
    assert [
        check.message
        for check in checks
        if check.message.startswith(("implementer:", "reviewer:"))
    ] == [
        "implementer: claude  permissions: managed  login: checked  denials: reported",
        "reviewer: codex  permissions: managed  login: checked  denials: reported",
    ]


def test_stand_in_codex_command_is_not_login_checked(ready_repo: Path):
    calls: list[list[str]] = []

    preflight.run(ready_repo, runner=successful_runner(calls))

    assert calls == [["whyline", "sync"]]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("- [ ] DUP: one\n- [ ] DUP: two\n", "duplicate task id"),
        ("- [x] APP-1: done\n  Details.\n", "no unchecked tasks in"),
    ],
)
def test_invalid_or_finished_plan_fails(ready_repo: Path, content: str, expected: str):
    (ready_repo / "plan.md").write_text(content)
    checks = preflight.run(ready_repo, allow_dirty=True, runner=successful_runner())
    assert any(check.status == "FAIL" and expected in check.message for check in checks)


def test_missing_plan_fails(ready_repo: Path):
    checks = preflight.run(
        ready_repo, Path("other.md"), allow_dirty=True, runner=successful_runner()
    )
    assert any(
        check.status == "FAIL" and check.message.startswith("plan file does not exist:")
        for check in checks
    )


def test_plan_style_warnings_are_per_task_and_do_not_count_as_failures(ready_repo: Path):
    (ready_repo / "plan.md").write_text(
        "- [ ] Build the cache\n- [ ] APP-2: Test it\n"
    )
    checks = preflight.run(ready_repo, allow_dirty=True, runner=successful_runner())
    warnings = [check for check in checks if check.status == "warn"]
    assert [check.message for check in warnings] == [
        "task 'Build' has no ID before a colon; its id is its first word",
        "task 'Build' has no detail lines",
        "task 'APP-2' has no detail lines",
    ]
    assert preflight.failures(warnings) == 0


def test_dirty_tree_fails_unless_allowed(ready_repo: Path):
    (ready_repo / "scratch.txt").write_text("dirty")
    blocked = preflight.run(ready_repo, runner=successful_runner())
    dirty = next(check for check in blocked if "working tree" in check.message)
    assert dirty == preflight.Check(
        "FAIL",
        "working tree has uncommitted changes",
        "commit or stash your changes, or pass --allow-dirty",
    )
    allowed = preflight.run(ready_repo, allow_dirty=True, runner=successful_runner())
    assert any(check.message == "working-tree check skipped (--allow-dirty)" for check in allowed)


def test_live_relay_fails_with_fix(ready_repo: Path, monkeypatch):
    marker = preflight.running.Running("codex", "APP-1", 1, "now", os.getpid())
    monkeypatch.setattr(preflight.running, "live", lambda root: marker)
    check = preflight.run(ready_repo, runner=successful_runner())[-1]
    assert check == preflight.Check(
        "FAIL",
        f"another relay is running here (pid {os.getpid()})",
        "wait for it, or run: whyline-relay stop",
    )


def test_report_format_order_summary_and_warning_exit_behavior(capsys):
    checks = [
        preflight.Check("ok", "first"),
        preflight.Check("warn", "second", "consider this"),
        preflight.Check("FAIL", "third", "do this"),
    ]
    preflight.print_checks(checks, stream=sys.stdout, include_ok=True, summary=True)
    assert capsys.readouterr().out.splitlines() == [
        "  ok    first",
        "  warn  second",
        "        fix: consider this",
        "  FAIL  third",
        "        fix: do this",
        "1 problem(s) found.",
    ]


@pytest.mark.parametrize(
    ("checks", "code", "last"),
    [
        ([preflight.Check("warn", "advisory")], cli.EXIT_OK, "All checks passed."),
        ([preflight.Check("FAIL", "broken")], cli.EXIT_ERROR, "1 problem(s) found."),
    ],
)
def test_doctor_exit_codes(checks, code, last, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli.preflight, "run", lambda *args, **kwargs: checks)
    assert cli.main(["doctor", "--repo", str(tmp_path)]) == code
    assert capsys.readouterr().out.splitlines()[-1] == last


@pytest.mark.parametrize("command", ["start", "resume"])
def test_launch_commands_stop_on_preflight_failure_without_mutating(
    ready_repo: Path, command: str, monkeypatch, capsys
):
    relay = ready_repo / ".whyline" / "relay"
    stop = relay / "STOP"
    stop.write_text("")
    if command == "resume":
        state.save(
            ready_repo,
            state.RelayState(
                plan=str(ready_repo / "plan.md"),
                branch="relay/plan",
                task_id="APP-1",
                round=1,
                base_commit="abc",
                paused_reason="paused",
                log_path="",
            ),
        )
    monkeypatch.setattr(
        cli,
        "_launch_checks",
        lambda *args, **kwargs: [preflight.Check("FAIL", "preflight broke", "fix it")],
    )
    monkeypatch.setattr(cli.gitcheck, "ensure_branch", lambda *args: pytest.fail("branch changed"))
    monkeypatch.setattr(
        cli.loop,
        "run_plan",
        lambda *args, **kwargs: pytest.fail("agent launched"),
    )

    code = cli.main([command, "--repo", str(ready_repo), "--allow-dirty"])

    assert code == cli.EXIT_ERROR
    assert stop.exists()
    assert capsys.readouterr().err.splitlines() == [
        "  FAIL  preflight broke",
        "        fix: fix it",
    ]


@pytest.mark.parametrize("command", ["start", "resume"])
def test_skip_checks_bypasses_preflight(
    ready_repo: Path, command: str, monkeypatch, capsys
):
    if command == "resume":
        state.save(
            ready_repo,
            state.RelayState(
                plan=str(ready_repo / "plan.md"),
                branch="relay/plan",
                task_id="APP-1",
                round=1,
                base_commit="abc",
                paused_reason="paused",
                log_path="",
            ),
        )
    monkeypatch.setattr(cli, "_launch_checks", lambda *args, **kwargs: pytest.fail("checks ran"))
    monkeypatch.setattr(cli.gitcheck, "ensure_branch", lambda *args: None)
    monkeypatch.setattr(cli.loop, "run_plan", lambda *args, **kwargs: [])

    code = cli.main(
        [command, "--repo", str(ready_repo), "--allow-dirty", "--skip-checks"]
    )
    assert code == cli.EXIT_OK, capsys.readouterr().err


def test_warning_is_printed_but_does_not_block_start(
    ready_repo: Path, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli,
        "_launch_checks",
        lambda *args, **kwargs: [
            preflight.Check("warn", "advisory", "consider it")
        ],
    )
    monkeypatch.setattr(cli.gitcheck, "ensure_branch", lambda *args: None)
    launched = []
    monkeypatch.setattr(
        cli.loop, "run_plan", lambda *args, **kwargs: launched.append(True) or []
    )

    code = cli.main(["start", "--repo", str(ready_repo), "--allow-dirty"])

    assert code == cli.EXIT_OK
    assert launched == [True]
    assert capsys.readouterr().err.splitlines() == [
        "  warn  advisory",
        "        fix: consider it",
    ]


def test_start_dry_run_never_runs_preflight_or_login(ready_repo: Path, monkeypatch):
    monkeypatch.setattr(cli, "_launch_checks", lambda *args, **kwargs: pytest.fail("checks ran"))
    monkeypatch.setattr(cli.whylinecmd, "sync", lambda *args, **kwargs: "PACKET")
    assert cli.main(["start", "--repo", str(ready_repo), "--dry-run"]) == cli.EXIT_OK
