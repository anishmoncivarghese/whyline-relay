import subprocess
import sys
from pathlib import Path

import pytest

from whyline_relay import adapters, cli, config, loop, plan, preflight
from whyline_relay.adapters import bypass


TASK = plan.Task(task_id="ADPT-9", text="ADPT-9: Guard commands", checked=False, line_index=0)
CODEX_FLAG = "--dangerously-bypass-approvals-and-sandbox"
CODEX_HOOK_FLAG = "--dangerously-bypass-hook-trust"
CLAUDE_FLAG = "--dangerously-skip-permissions"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    _git(tmp_path, "init", "-b", "relay/plan")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        f'[agents.codex]\ncommand = ["{sys.executable}", "{CODEX_FLAG}"]\n',
        encoding="utf-8",
    )
    (tmp_path / "plan.md").write_text("- [ ] ADPT-9: Guard commands\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "ready")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda *args, **kwargs: "PACKET")
    return tmp_path


@pytest.mark.parametrize(
    ("adapter_name", "command", "expected"),
    [
        ("codex", [CODEX_FLAG], [CODEX_FLAG]),
        ("codex", [f"{CODEX_FLAG}=yes"], [f"{CODEX_FLAG}=yes"]),
        ("codex", [CODEX_HOOK_FLAG], [CODEX_HOOK_FLAG]),
        ("codex", [f"{CODEX_HOOK_FLAG}=yes"], [f"{CODEX_HOOK_FLAG}=yes"]),
        ("claude", [CLAUDE_FLAG], [CLAUDE_FLAG]),
        ("claude", [f"{CLAUDE_FLAG}=yes"], [f"{CLAUDE_FLAG}=yes"]),
    ],
)
def test_each_refused_flag_form_is_found_only_for_its_adapter(
    adapter_name: str, command: list[str], expected: list[str]
):
    assert bypass.find(adapter_name, command) == expected
    other = "claude" if adapter_name == "codex" else "codex"
    assert bypass.find(other, command) == []


@pytest.mark.parametrize(
    ("adapter_name", "command", "expected"),
    [
        ("codex", ["codex", "-s", "danger-full-access"], ["-s danger-full-access"]),
        (
            "codex",
            ["codex", "--sandbox=danger-full-access"],
            ["--sandbox=danger-full-access"],
        ),
        (
            "claude",
            ["claude", "--permission-mode", "bypassPermissions"],
            ["--permission-mode bypassPermissions"],
        ),
        (
            "claude",
            ["claude", "--permission-mode=bypassPermissions"],
            ["--permission-mode=bypassPermissions"],
        ),
    ],
)
def test_permission_mode_forms_are_found(
    adapter_name: str, command: list[str], expected: list[str]
):
    assert bypass.find(adapter_name, command) == expected


def test_defaults_are_clean():
    for name, adapter in adapters.BUILTIN.items():
        assert bypass.find(name, adapter.default_command) == []
        assert bypass.find(name, config.DEFAULTS["agents"][name]) == []


def test_generic_commands_are_not_inspected():
    assert bypass.find("generic", [CODEX_FLAG, CLAUDE_FLAG]) == []
    assert bypass.find("other", [CODEX_FLAG, CLAUDE_FLAG]) == []


def test_run_task_refuses_before_launching(repo: Path, monkeypatch):
    settings = config.load(repo)

    def launched(*args, **kwargs):
        pytest.fail("agents.run must not be called")

    monkeypatch.setattr(loop.agents, "run", launched)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(
            repo,
            settings,
            TASK,
            base_commit=loop.gitcheck.head_commit(repo),
            echo=False,
        )

    assert raised.value.reason == (
        f"refusing to run codex: its command contains a permission-bypass flag "
        f"({CODEX_FLAG}). The relay never runs an agent that way; remove it from "
        ".whyline/relay/config.toml"
    )
    assert raised.value.log_path is None


def test_preflight_refuses_a_bypass_command(repo: Path):
    completed = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", "")
    checks = preflight.run(repo, runner=completed)
    refusal = next(check for check in checks if CODEX_FLAG in check.message)

    assert refusal.status == "FAIL"
    assert refusal.hint == "remove it from .whyline/relay/config.toml"
    assert refusal.message == (
        f"refusing to run codex: its command contains a permission-bypass flag "
        f"({CODEX_FLAG}). The relay never runs an agent that way"
    )


def test_start_skip_checks_still_refuses_before_launching(
    repo: Path, monkeypatch, capsys
):
    def launched(*args, **kwargs):
        pytest.fail("agents.run must not be called")

    monkeypatch.setattr(loop.agents, "run", launched)
    code = cli.main(
        ["start", "--repo", str(repo), "--allow-dirty", "--skip-checks"]
    )

    assert code != cli.EXIT_OK
    assert CODEX_FLAG in capsys.readouterr().err


AGY_FLAG = "--dangerously-skip-permissions"
GROK_MODE_FLAG = "--permission-mode"
GROK_MODE_OFF = "bypassPermissions"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["agy", AGY_FLAG], [AGY_FLAG]),
        (["agy", f"{AGY_FLAG}=yes"], [f"{AGY_FLAG}=yes"]),
        (["agy", "-p"], []),
    ],
)
def test_a_known_binary_configured_as_generic_is_still_checked_for_its_own_flag(
    command: list[str], expected: list[str]
):
    # adapter_name is always "generic" here -- there is no registered adapter
    # to look up FLAGS by name for. The binary itself (command[0]) is what we
    # actually know a bypass flag for, so the check runs off that instead.
    assert bypass.find("generic", command) == expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            ["grok", GROK_MODE_FLAG, GROK_MODE_OFF, "-p"],
            [f"{GROK_MODE_FLAG} {GROK_MODE_OFF}"],
        ),
        (
            ["grok", f"{GROK_MODE_FLAG}={GROK_MODE_OFF}", "-p"],
            [f"{GROK_MODE_FLAG}={GROK_MODE_OFF}"],
        ),
        (["grok", GROK_MODE_FLAG, "dontAsk", "-p"], []),
    ],
)
def test_grok_configured_as_generic_is_checked_for_its_bypass_mode(
    command: list[str], expected: list[str]
):
    assert bypass.find("generic", command) == expected


def test_an_unknown_binary_configured_as_generic_is_still_not_inspected():
    # A tool this project has never measured has no known flag vocabulary to
    # check against -- this is the same, unchanged case test_generic_commands
    # _are_not_inspected already covers for a command with no real binary at
    # position 0.
    assert bypass.find("generic", ["aider", "--message", CLAUDE_FLAG]) == []


def test_preflight_refuses_antigravity_configured_as_generic_with_its_bypass_flag(
    tmp_path: Path,
):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        '[roles]\nimplementer = "codex"\nreviewer = "antigravity"\n'
        f'[agents.codex]\ncommand = ["{sys.executable}", "codex-role"]\n'
        '[agents.antigravity]\nadapter = "generic"\n'
        f'command = ["agy", "-p", "{AGY_FLAG}"]\n'
    )
    (tmp_path / "plan.md").write_text("- [ ] T-1: build it\n  Include tests.\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "setup")

    completed = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", "")
    checks = preflight.run(tmp_path, runner=completed)
    refusal = next(check for check in checks if AGY_FLAG in check.message)
    assert refusal.status == "FAIL"
