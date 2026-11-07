import json
from pathlib import Path

import pytest

from whyline_relay import config, failover, recipes, roles


def _write(root: Path, text: str) -> None:
    relay = root / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "config.toml").write_text(text)


def test_current_roles_is_the_legacy_pair_without_a_pipeline(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\nreviewer = "claude"\n',
    )
    settings = config.load(tmp_path)
    assert roles.current_roles(settings) == {
        "implementer": "codex",
        "reviewer": "claude",
    }


def test_current_roles_is_the_pipelines_own_roles_when_configured(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\ntester = "claude"\nreviewer = "claude"\n'
        '[pipeline]\ndefault_profile = "full"\n'
        '[pipeline.profiles]\nfull = ["only"]\n'
        '[pipeline.stages.only]\nrole = "implementer"\nprompt = "implement"\n'
        '[pipeline.stages.only.on]\nready = "@complete"\n',
    )
    settings = config.load(tmp_path)
    assert roles.current_roles(settings) == {
        "implementer": "codex",
        "tester": "claude",
        "reviewer": "claude",
    }


def test_status_reports_each_configured_pipeline_role(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\ntester = "claude"\nreviewer = "claude"\n'
        '[pipeline]\ndefault_profile = "full"\n'
        '[pipeline.profiles]\nfull = ["only"]\n'
        '[pipeline.stages.only]\nrole = "implementer"\nprompt = "implement"\n'
        '[pipeline.stages.only.on]\nready = "@complete"\n',
    )
    settings = config.load(tmp_path)
    assert roles.status(tmp_path, settings) == (
        "implementer: codex\ntester: claude\nreviewer: claude"
    )


def test_set_role_changes_an_existing_pipeline_role(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\ntester = "claude"\nreviewer = "claude"\n'
        '[pipeline]\ndefault_profile = "full"\n'
        '[pipeline.profiles]\nfull = ["only"]\n'
        '[pipeline.stages.only]\nrole = "implementer"\nprompt = "implement"\n'
        '[pipeline.stages.only.on]\nready = "@complete"\n',
    )
    settings = config.load(tmp_path)
    message = roles.set_role(
        tmp_path, settings, "tester", agent="codex", model=None
    )
    assert message == "tester set to codex"
    assert roles.current_roles(config.load(tmp_path))["tester"] == "codex"


def test_set_role_changes_the_agent_and_preserves_everything_else(tmp_path):
    _write(
        tmp_path,
        '# a comment\n[roles]\nimplementer = "codex"\nreviewer = "claude"\n\n'
        '[agents.codex]\ncommand = ["codex"]\n\n'
        '[agents.claude]\ncommand = ["claude"]\n',
    )
    settings = config.load(tmp_path)
    message = roles.set_role(
        tmp_path, settings, "reviewer", agent="codex", model=None
    )
    assert message == "reviewer set to codex"
    reloaded = config.load(tmp_path)
    assert roles.current_roles(reloaded) == {
        "implementer": "codex",
        "reviewer": "codex",
    }
    text = config.config_path(tmp_path).read_text()
    assert "# a comment" in text
    assert text.count("[agents.codex]") == 1
    assert text.count("[agents.claude]") == 1


def test_set_role_with_a_model_writes_the_agents_block(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\nreviewer = "claude"\n',
    )
    settings = config.load(tmp_path)
    message = roles.set_role(
        tmp_path, settings, "implementer", agent="claude", model="opus"
    )
    assert message == "implementer set to claude (model opus)"
    reloaded = config.load(tmp_path)
    assert reloaded.agents["claude"] == [
        *config.DEFAULTS["agents"]["claude"],
        "--model",
        "opus",
        "--append-system-prompt",
        recipes.CLAUDE_RULES,
    ]


def test_set_role_refuses_an_unknown_role(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\nreviewer = "claude"\n',
    )
    settings = config.load(tmp_path)
    with pytest.raises(roles.RoleSetError, match="not a configured role"):
        roles.set_role(
            tmp_path, settings, "nonexistent", agent="codex", model=None
        )


def test_set_role_refuses_an_unknown_agent(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\nreviewer = "claude"\n',
    )
    settings = config.load(tmp_path)
    with pytest.raises(roles.RoleSetError, match="not a built-in agent"):
        roles.set_role(
            tmp_path,
            settings,
            "implementer",
            agent="nonexistent-thing",
            model=None,
        )


def test_set_role_refuses_a_model_on_a_non_builtin_agent(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\nreviewer = "claude"\n'
        '[agents.aider]\nadapter = "generic"\ncommand = ["aider"]\n',
    )
    settings = config.load(tmp_path)
    with pytest.raises(roles.RoleSetError, match="only applies to a built-in"):
        roles.set_role(
            tmp_path, settings, "implementer", agent="aider", model="sonnet"
        )


def test_set_role_prompts_interactively_when_agent_and_model_are_omitted(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\nreviewer = "claude"\n',
    )
    settings = config.load(tmp_path)
    answers = iter(["claude", "haiku"])
    message = roles.set_role(
        tmp_path,
        settings,
        "implementer",
        agent=None,
        model=None,
        confirm=lambda _prompt: next(answers),
    )
    assert message == "implementer set to claude (model haiku)"


def test_set_role_keeps_the_current_agent_on_a_blank_answer(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\nreviewer = "claude"\n',
    )
    settings = config.load(tmp_path)
    answers = iter(["", ""])
    message = roles.set_role(
        tmp_path,
        settings,
        "implementer",
        agent=None,
        model=None,
        confirm=lambda _prompt: next(answers),
    )
    assert message == "implementer set to codex"


def test_set_role_with_agent_given_and_no_model_never_prompts(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\nreviewer = "claude"\n',
    )
    settings = config.load(tmp_path)

    def confirm_must_not_be_called(_prompt):
        raise AssertionError("set_role prompted when --agent was already given")

    message = roles.set_role(
        tmp_path,
        settings,
        "implementer",
        agent="codex",
        model=None,
        confirm=confirm_must_not_be_called,
    )
    assert message == "implementer set to codex"


def test_set_role_prefills_the_model_prompt_from_whyline_model_json(tmp_path):
    from whyline_relay import config, roles
    target = tmp_path / ".whyline" / "model.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"codex": "gpt-5-codex"}')
    (tmp_path / ".whyline" / "relay").mkdir(parents=True)
    config_path = tmp_path / ".whyline" / "relay" / "config.toml"
    config_path.write_text("")
    settings = config.load(tmp_path)
    answers = iter(["codex", ""])
    result = roles.set_role(
        tmp_path, settings, "implementer", agent=None, model=None,
        confirm=lambda prompt: next(answers),
    )
    assert "gpt-5-codex" in config_path.read_text()
    assert result == "implementer set to codex (model gpt-5-codex)"
def test_set_role_eof_with_a_preset_falls_back_to_it(tmp_path):
    from whyline_relay import config, roles
    target = tmp_path / ".whyline" / "model.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"codex": "gpt-5-codex"}')
    (tmp_path / ".whyline" / "relay").mkdir(parents=True)
    config_path = tmp_path / ".whyline" / "relay" / "config.toml"
    config_path.write_text("")
    settings = config.load(tmp_path)

    def confirm(prompt):
        if "Agent for implementer" in prompt:
            return "codex"
        raise EOFError

    result = roles.set_role(
        tmp_path, settings, "implementer", agent=None, model=None,
        confirm=confirm,
    )
    assert "gpt-5-codex" in config_path.read_text()
    assert result == "implementer set to codex (model gpt-5-codex)"


def test_status_shows_tried_history_when_present(tmp_path):
    from whyline_relay import config, failover, roles
    settings = config.load(tmp_path)
    failover.write_override(
        tmp_path, "implementer",
        failover.ActiveOverride(
            "grok", "codex", "rate-limit", "2026-01-01T00:00:00",
            tried=["codex", "claude"],
        ),
    )
    output = roles.status(tmp_path, settings)
    assert "tried: codex, claude" in output


def test_status_omits_tried_when_there_is_only_the_current_backup(tmp_path):
    from whyline_relay import config, failover, roles
    settings = config.load(tmp_path)
    failover.write_override(
        tmp_path, "implementer",
        failover.ActiveOverride("grok", "codex", "rate-limit", "2026-01-01T00:00:00"),
    )
    output = roles.status(tmp_path, settings)
    assert "tried:" not in output


def test_status_names_the_agent_that_hit_the_limit_not_the_configured_one(
    tmp_path,
):
    _write(tmp_path, '[roles]\nimplementer = "antigravity"\nreviewer = "codex"\n')
    settings = config.load(tmp_path)
    failover.write_override(
        tmp_path, "implementer",
        failover.ActiveOverride("claude", "grok", "rate-limit", "2026-10-21T00:00:00"),
    )
    output = roles.status(tmp_path, settings)
    assert (
        "implementer: antigravity, currently claude (rate-limit: grok "
        "(configured is now antigravity) hit a usage or rate limit, "
        "since 2026-10-21T00:00:00)"
    ) in output


def test_status_omits_the_note_when_backup_for_is_the_configured_agent(tmp_path):
    _write(tmp_path, '[roles]\nimplementer = "grok"\nreviewer = "codex"\n')
    settings = config.load(tmp_path)
    failover.write_override(
        tmp_path, "implementer",
        failover.ActiveOverride("claude", "grok", "rate-limit", "2026-10-21T00:00:00"),
    )
    output = roles.status(tmp_path, settings)
    assert "rate-limit: grok hit a usage or rate limit" in output
    assert "configured is now" not in output


def test_with_model_replaces_the_value_after_the_flag():
    command = ["claude", "-p", "--model", "sonnet", "--verbose"]
    assert config.with_model(command, ("--model",), "opus") == [
        "claude", "-p", "--model", "opus", "--verbose",
    ]
    assert command[3] == "sonnet"


def test_with_model_appends_when_there_is_no_flag():
    assert config.with_model(["codex", "exec"], ("--model",), "o3") == [
        "codex", "exec", "--model", "o3",
    ]


def test_with_model_replaces_only_the_last_flag():
    command = ["x", "--model", "a", "--model", "b", "--tail"]
    assert config.with_model(command, ("--model",), "c") == [
        "x", "--model", "a", "--model", "c", "--tail",
    ]


def test_with_model_handles_a_two_token_flag():
    command = ["agent", "-c", "model", "small", "-c", "other", "v"]
    assert config.with_model(command, ("-c", "model"), "large") == [
        "agent", "-c", "model", "large", "-c", "other", "v",
    ]
    assert config.with_model(["agent", "run"], ("-c", "model"), "large") == [
        "agent", "run", "-c", "model", "large",
    ]


def test_with_model_ignores_a_trailing_flag_with_no_value():
    assert config.with_model(["x", "--model"], ("--model",), "opus") == [
        "x", "--model", "--model", "opus",
    ]


def test_load_keeps_each_agents_model_flag_and_configured_model(tmp_path):
    _write(
        tmp_path,
        '[agents.claude]\nmodel = "sonnet"\n'
        '[agents.tool]\nadapter = "generic"\ncommand = ["tool"]\n'
        'model_flag = ["-c", "model"]\n',
    )
    settings = config.load(tmp_path)
    assert settings.model_flags["claude"] == ("--model",)
    assert settings.model_flags["codex"] == ("--model",)
    assert settings.model_flags["tool"] == ("-c", "model")
    assert settings.models["claude"] == "sonnet"
    assert "codex" not in settings.models


def _sonnet_claude(root: Path) -> config.Config:
    _write(root, '[agents.claude]\nmodel = "sonnet"\n')
    return config.load(root)


def _model_file(root: Path) -> Path:
    return root / ".whyline" / "relay" / "model-overrides.json"


def test_switch_model_writes_the_override_file(tmp_path):
    settings = _sonnet_claude(tmp_path)
    message = roles.switch_model(tmp_path, settings, "claude", "opus")
    assert message == "claude → opus from its next turn."
    record = json.loads(_model_file(tmp_path).read_text())
    assert record["claude"]["model"] == "opus"
    assert record["claude"]["keep"] is False
    assert record["claude"]["since"]
    assert not list(_model_file(tmp_path).parent.glob("*.tmp"))
    # config.toml is untouched without keep.
    assert 'model = "sonnet"' in config.config_path(tmp_path).read_text()


def test_reset_and_the_configured_model_both_remove_the_entry(tmp_path):
    settings = _sonnet_claude(tmp_path)
    roles.switch_model(tmp_path, settings, "claude", "opus")
    roles.switch_model(tmp_path, settings, "codex", "o3")
    assert roles.switch_model(tmp_path, settings, "claude", "sonnet") == (
        "claude back to sonnet from its next turn."
    )
    assert set(roles.read_model_overrides(tmp_path)) == {"codex"}
    assert roles.switch_model(tmp_path, settings, "codex", "") == (
        "codex back to its default from its next turn."
    )
    assert not _model_file(tmp_path).exists()


def test_keep_writes_the_model_to_config_toml(tmp_path):
    settings = _sonnet_claude(tmp_path)
    roles.switch_model(tmp_path, settings, "claude", "opus", keep=True)
    assert roles.read_model_overrides(tmp_path)["claude"].keep is True
    assert config.load(tmp_path).models["claude"] == "opus"
    text = config.config_path(tmp_path).read_text()
    assert 'model = "sonnet"' not in text


def test_an_agent_without_a_model_flag_is_refused(tmp_path):
    _write(tmp_path, '[agents.tool]\nadapter = "generic"\ncommand = ["tool"]\n')
    settings = config.load(tmp_path)
    with pytest.raises(roles.RoleSetError) as raised:
        roles.switch_model(tmp_path, settings, "tool", "big")
    assert str(raised.value) == (
        "tool has no way to set a model; set model_flag under [agents.tool]"
    )
    assert not _model_file(tmp_path).exists()


def test_an_agent_not_set_up_is_refused(tmp_path):
    settings = _sonnet_claude(tmp_path)
    with pytest.raises(roles.RoleSetError):
        roles.switch_model(tmp_path, settings, "nobody", "big")


def test_status_shows_a_switched_model(tmp_path):
    settings = _sonnet_claude(tmp_path)
    roles.switch_model(tmp_path, settings, "claude", "opus")
    roles.switch_model(tmp_path, settings, "codex", "o3")
    lines = roles.status(tmp_path, settings).splitlines()
    assert "claude: opus (switched from sonnet)" in lines
    assert "codex: o3 (switched from its default)" in lines


def test_a_kept_switch_is_not_shown_once_config_toml_has_it(tmp_path):
    settings = _sonnet_claude(tmp_path)
    roles.switch_model(tmp_path, settings, "claude", "opus", keep=True)
    assert "switched from" not in roles.status(tmp_path, config.load(tmp_path))
