from pathlib import Path

from whyline_relay import config


def test_defaults_when_no_file(tmp_path: Path):
    loaded = config.load(tmp_path)
    assert loaded.plan == "plan.md"
    assert loaded.max_rounds == 3
    assert loaded.timeout_minutes == 30
    assert loaded.branch_prefix == "relay/"
    assert loaded.agents["codex"][0] == "codex"
    assert loaded.agents["claude"][0] == "claude"
    assert loaded.status_map["review"] == "ready-for-review"


def test_file_overrides_only_given_keys(tmp_path: Path):
    target = tmp_path / ".whyline" / "relay"
    target.mkdir(parents=True)
    (target / "config.toml").write_text(
        'max_rounds = 5\n\n[agents.codex]\ncommand = ["codex", "exec"]\n'
    )
    loaded = config.load(tmp_path)
    assert loaded.max_rounds == 5
    assert loaded.agents["codex"] == ["codex", "exec"]
    assert loaded.timeout_minutes == 30
    assert loaded.agents["claude"][0] == "claude"


def test_malformed_toml_raises_config_error(tmp_path: Path):
    target = tmp_path / ".whyline" / "relay"
    target.mkdir(parents=True)
    (target / "config.toml").write_text("max_rounds = [unclosed\n")
    try:
        config.load(tmp_path)
    except config.ConfigError as error:
        assert "config.toml" in str(error)
    else:
        raise AssertionError("expected ConfigError")


def test_default_claude_command_passes_the_relay_permissions(tmp_path: Path):
    """Project settings are ignored in a workspace never trusted interactively,
    so the permissions travel with the command instead."""
    command = config.load(tmp_path).agents["claude"]
    assert command[command.index("--settings") + 1] == ".whyline/relay/claude-settings.json"
