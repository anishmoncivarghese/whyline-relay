import re
from pathlib import Path

import pytest

from whyline_relay import config


def write(root: Path, text: str) -> None:
    target = root / ".whyline" / "relay"
    target.mkdir(parents=True)
    (target / "config.toml").write_text(text)


def test_defaults_are_the_0_2_1_commands():
    assert config.DEFAULTS["agents"]["codex"] == [
        "codex",
        "exec",
        "-s",
        "workspace-write",
        "--color",
        "never",
    ]
    assert config.DEFAULTS["agents"]["claude"] == [
        "claude",
        "-p",
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "json",
        "--settings",
        ".whyline/relay/claude-settings.json",
    ]


def test_no_config_file_means_the_default_roles(tmp_path: Path):
    assert config.load(tmp_path).roles == config.Roles("codex", "claude")


def test_a_generic_agent_needs_adapter_and_command(tmp_path: Path):
    write(tmp_path, '[agents.aider]\ncommand = ["aider", "--message"]\n')
    with pytest.raises(config.ConfigError, match=r"agent 'aider' is not built in"):
        config.load(tmp_path)


def test_roles_may_name_a_configured_generic_agent(tmp_path: Path):
    write(
        tmp_path,
        '[roles]\nreviewer = "aider"\n[agents.aider]\n'
        'adapter = "generic"\ncommand = ["aider", "--message"]\n',
    )
    loaded = config.load(tmp_path)
    assert loaded.roles == config.Roles("codex", "aider")
    assert loaded.agents["aider"] == ["aider", "--message"]
    assert config.adapter_for(loaded, "aider").name == "generic"
    assert config.adapter_for(loaded, "codex").name == "codex"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            '[roles]\nobserver = "claude"\n',
            "[roles] has an unknown key 'observer' (use implementer or reviewer)",
        ),
        (
            "[roles]\nimplementer = 42\n",
            "[roles] implementer must be a string",
        ),
        (
            '[agents.aider]\ncommand = ["aider"]\n',
            "agent 'aider' is not built in: set adapter = \"generic\" and a command "
            "under [agents.aider]",
        ),
        (
            '[agents.codex]\nadapter = "generic"\ncommand = ["codex"]\n',
            "agent 'codex' is built in and cannot set adapter",
        ),
        (
            '[agents.aider]\nadapter = "mystery"\ncommand = ["aider"]\n',
            '[agents.aider] adapter must be "generic" or a built-in agent '
            "(claude, codex), not 'mystery'",
        ),
        (
            '[agents.aider]\nadapter = "generic"\ncommand = []\n',
            "[agents.aider] needs a non-empty command",
        ),
        (
            '[roles]\nimplementer = "gemini"\n',
            "role 'implementer' names 'gemini', which is not a built-in agent "
            "(claude, codex) or a configured generic agent",
        ),
    ],
)
def test_role_and_agent_config_errors_are_precise(
    tmp_path: Path, text: str, message: str
):
    write(tmp_path, text)
    with pytest.raises(config.ConfigError, match=re.escape(message)):
        config.load(tmp_path)


def test_config_can_still_be_built_without_roles_or_adapters():
    built = config.Config(
        plan="plan.md",
        max_rounds=3,
        timeout_minutes=30,
        branch_prefix="relay/",
        agents={"codex": ["codex"], "claude": ["claude"]},
        status_map={},
    )
    assert built.roles == config.Roles()
    assert built.adapters == {}


def test_no_backup_table_means_no_backups(tmp_path):
    write(tmp_path, "")
    assert config.load(tmp_path).backups == {}


def test_a_role_backup_is_parsed(tmp_path):
    write(tmp_path, '[roles.backup]\nimplementer = "claude"\n')
    loaded = config.load(tmp_path)
    assert loaded.backups == {"implementer": "claude"}
    assert loaded.roles.implementer == "codex"  # the primary is untouched


def test_a_backup_may_be_a_configured_generic_agent(tmp_path):
    write(
        tmp_path,
        '[roles.backup]\nreviewer = "aider"\n[agents.aider]\n'
        'adapter = "generic"\ncommand = ["aider"]\n',
    )
    assert config.load(tmp_path).backups == {"reviewer": "aider"}


def test_a_backup_naming_the_same_agent_as_its_role_is_refused(tmp_path):
    write(tmp_path, '[roles.backup]\nimplementer = "codex"\n')
    with pytest.raises(config.ConfigError, match="cannot be the same as its own agent"):
        config.load(tmp_path)


def test_a_backup_naming_an_unknown_agent_is_refused(tmp_path):
    write(tmp_path, '[roles.backup]\nimplementer = "gemini"\n')
    with pytest.raises(
        config.ConfigError,
        match="not a built-in agent .* or a configured generic agent",
    ):
        config.load(tmp_path)


def test_an_unknown_key_under_roles_backup_is_refused(tmp_path):
    write(tmp_path, '[roles.backup]\nplanner = "claude"\n')
    with pytest.raises(
        config.ConfigError,
        match="\\[roles.backup\\] has an unknown key 'planner'",
    ):
        config.load(tmp_path)


def test_a_non_string_backup_is_refused(tmp_path):
    write(tmp_path, "[roles.backup]\nimplementer = 3\n")
    with pytest.raises(
        config.ConfigError,
        match="\\[roles.backup\\] implementer must be a string",
    ):
        config.load(tmp_path)


def test_config_built_by_hand_still_works_without_backups():
    cfg = config.Config(
        plan="plan.md",
        max_rounds=3,
        timeout_minutes=30,
        branch_prefix="relay/",
        agents={},
        status_map={},
    )
    assert cfg.backups == {}


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


def test_a_custom_name_may_alias_the_claude_adapter(tmp_path: Path):
    write(
        tmp_path,
        '[agents.claude-opus]\nadapter = "claude"\n',
    )
    loaded = config.load(tmp_path)
    assert loaded.agents["claude-opus"] == config.DEFAULTS["agents"]["claude"]
    assert config.adapter_for(loaded, "claude-opus").name == "claude"


def test_a_claude_variant_can_override_command_and_add_a_model(tmp_path: Path):
    write(
        tmp_path,
        '[agents.claude-opus]\nadapter = "claude"\n'
        'command = ["claude", "-p"]\nmodel = "opus"\n',
    )
    loaded = config.load(tmp_path)
    assert loaded.agents["claude-opus"] == ["claude", "-p", "--model", "opus"]
    assert config.adapter_for(loaded, "claude-opus").name == "claude"


def test_a_codex_variant_with_no_command_gets_the_default_plus_the_model(
    tmp_path: Path,
):
    write(tmp_path, '[agents.codex-fast]\nadapter = "codex"\nmodel = "gpt-5-mini"\n')
    loaded = config.load(tmp_path)
    assert loaded.agents["codex-fast"] == [
        *config.DEFAULTS["agents"]["codex"],
        "--model",
        "gpt-5-mini",
    ]


def test_the_literal_built_in_name_can_also_take_a_model(tmp_path: Path):
    write(tmp_path, '[agents.claude]\nmodel = "haiku"\n')
    loaded = config.load(tmp_path)
    assert loaded.agents["claude"] == [
        *config.DEFAULTS["agents"]["claude"],
        "--model",
        "haiku",
    ]


def test_model_on_a_generic_agent_is_refused(tmp_path: Path):
    write(
        tmp_path,
        '[agents.aider]\nadapter = "generic"\ncommand = ["aider"]\nmodel = "x"\n',
    )
    with pytest.raises(
        config.ConfigError, match="cannot set model: the generic adapter"
    ):
        config.load(tmp_path)


def test_a_non_string_model_is_refused(tmp_path: Path):
    write(tmp_path, "[agents.claude]\nmodel = 3\n")
    with pytest.raises(config.ConfigError, match="model must be a non-empty string"):
        config.load(tmp_path)


def test_an_empty_model_is_refused(tmp_path: Path):
    write(tmp_path, '[agents.claude]\nmodel = ""\n')
    with pytest.raises(config.ConfigError, match="model must be a non-empty string"):
        config.load(tmp_path)


def test_an_unrecognised_adapter_value_still_names_both_valid_options(
    tmp_path: Path,
):
    write(tmp_path, '[agents.aider]\nadapter = "mystery"\ncommand = ["aider"]\n')
    with pytest.raises(
        config.ConfigError,
        match=r'adapter must be "generic" or a built-in agent \(claude, codex\)',
    ):
        config.load(tmp_path)


def test_a_claude_variant_is_login_checked_and_bypass_checked_like_claude_itself(
    tmp_path: Path,
):
    from whyline_relay import preflight
    from whyline_relay.adapters.base import Manages

    write(
        tmp_path,
        '[roles]\nreviewer = "claude-opus"\n'
        '[agents.claude-opus]\nadapter = "claude"\nmodel = "opus"\n',
    )
    loaded = config.load(tmp_path)
    in_use = preflight._agents_in_use(loaded)
    assert "claude-opus" in in_use
    command, backup_for = in_use["claude-opus"]
    assert command[0] == "claude" and backup_for is None
    adapter = config.adapter_for(loaded, "claude-opus")
    assert adapter.login_argv == ("claude", "auth", "status")
    assert adapter.manages == Manages(True, True, True)
