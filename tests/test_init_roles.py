import subprocess
from pathlib import Path

import pytest

from whyline_relay import cli, config, preflight, remove


GOLDEN = Path(__file__).parent / "golden"


def _init(root: Path, *roles: str) -> int:
    return cli.main(["init", "--repo", str(root), "--yes", *roles])


def _successful_runner(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 0, "", "")


def test_default_init_matches_0_2_1_goldens(tmp_path: Path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\n")

    assert _init(tmp_path) == cli.EXIT_OK

    relay = tmp_path / ".whyline" / "relay"
    assert (relay / "config.toml").read_bytes() == (
        GOLDEN / "init_default_0.2.1.config.toml"
    ).read_bytes()
    assert (relay / "claude-settings.json").read_bytes() == (
        GOLDEN / "init_default_0.2.1.claude-settings.json"
    ).read_bytes()
    actual_stdout = capsys.readouterr().out.replace(str(tmp_path.resolve()), "{ROOT}")
    assert actual_stdout.encode() == (
        GOLDEN / "init_default_0.2.1.stdout"
    ).read_bytes()


def test_swapped_roles_write_both_agents_and_claude_permissions(
    tmp_path: Path,
):
    assert _init(
        tmp_path, "--implementer", "claude", "--reviewer", "codex"
    ) == cli.EXIT_OK

    relay = tmp_path / ".whyline" / "relay"
    text = (relay / "config.toml").read_text()
    assert config.load(tmp_path).roles == config.Roles("claude", "codex")
    assert "[agents.codex]" in text
    assert "[agents.claude]" in text
    assert (relay / "claude-settings.json").is_file()


def test_claude_in_both_roles_writes_only_claude_agent_and_permissions(
    tmp_path: Path, capsys
):
    assert _init(
        tmp_path, "--implementer", "claude", "--reviewer", "claude"
    ) == cli.EXIT_OK

    relay = tmp_path / ".whyline" / "relay"
    text = (relay / "config.toml").read_text()
    assert "[agents.codex]" not in text
    assert "[agents.claude]" in text
    assert (relay / "claude-settings.json").is_file()
    out = capsys.readouterr().out
    assert out.count("Proposed ") == 1
    assert "Proposed " + str(relay / "claude-settings.json") in out


def test_codex_in_both_roles_needs_no_claude_settings_and_can_be_removed(
    tmp_path: Path, capsys
):
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    assert _init(
        tmp_path, "--implementer", "codex", "--reviewer", "codex"
    ) == cli.EXIT_OK

    relay = tmp_path / ".whyline" / "relay"
    text = (relay / "config.toml").read_text()
    assert not (relay / "claude-settings.json").exists()
    assert "--settings" not in text
    assert "Proposed " not in capsys.readouterr().out
    checks = preflight.run(
        tmp_path, allow_dirty=True, runner=_successful_runner
    )
    assert checks[2] == preflight.Check("ok", "relay setup is complete")

    assert remove.run(tmp_path, assume_yes=True, force=False) == 0
    assert not relay.exists()


def test_init_rejects_a_generic_implementer_without_writing(
    tmp_path: Path,
):
    with pytest.raises(SystemExit) as raised:
        _init(tmp_path, "--implementer", "aider")

    assert raised.value.code == 2
    assert not (tmp_path / ".whyline" / "relay").exists()


def test_only_reviewer_given_keeps_default_implementer_and_writes_roles(
    tmp_path: Path,
):
    assert _init(tmp_path, "--reviewer", "codex") == cli.EXIT_OK

    text = config.config_path(tmp_path).read_text()
    assert config.load(tmp_path).roles == config.Roles("codex", "codex")
    assert "[roles]" in text


def test_init_role_flags_have_the_required_help_and_builtin_choices():
    parser = cli.build_parser()
    init_parser = next(
        action for action in parser._actions if action.dest == "command"
    ).choices["init"]
    actions = {action.dest: action for action in init_parser._actions}

    assert actions["implementer"].help == (
        "Which built-in agent implements (default: codex)"
    )
    assert actions["reviewer"].help == (
        "Which built-in agent reviews and commits (default: claude)"
    )
    assert actions["implementer"].choices == ["claude", "codex"]
    assert actions["reviewer"].choices == ["claude", "codex"]
