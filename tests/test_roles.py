from pathlib import Path

import pytest

from whyline_relay import config, roles


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
