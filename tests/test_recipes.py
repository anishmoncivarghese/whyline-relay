from pathlib import Path

from whyline_relay import chat, config, recipes


def _write(root: Path, text: str) -> None:
    path = root / ".whyline" / "relay" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_with_no_config_grok_and_antigravity_are_generic_agents(tmp_path):
    settings = config.load(tmp_path)
    assert settings.agents["grok"] == list(recipes.RECIPES["grok"])
    assert settings.agents["antigravity"] == list(recipes.RECIPES["antigravity"])
    assert settings.adapters["grok"] == "generic"
    assert settings.adapters["antigravity"] == "generic"


def test_recipes_end_with_the_prompt_flag(tmp_path):
    for command in recipes.RECIPES.values():
        assert command[-1] == "-p"


def test_a_repos_own_agent_table_wins(tmp_path):
    _write(tmp_path, '[agents.grok]\nadapter = "generic"\ncommand = ["grok", "-p"]\n')
    assert config.load(tmp_path).agents["grok"] == ["grok", "-p"]


def test_the_old_agy_alias_keeps_its_own_command(tmp_path):
    _write(tmp_path, '[agents.agy]\nadapter = "generic"\ncommand = ["agy", "mine", "-p"]\n')
    settings = config.load(tmp_path)
    assert "antigravity" not in settings.agents
    assert chat.resolve_command(settings, "antigravity") == ["agy", "mine", "-p"]


def test_grok_can_take_a_relay_role_with_no_agent_table(tmp_path):
    _write(tmp_path, '[roles]\nimplementer = "grok"\nreviewer = "antigravity"\n')
    settings = config.load(tmp_path)
    assert settings.roles.implementer == "grok"
    assert settings.roles.reviewer == "antigravity"


def test_chat_resolves_grok_with_no_config(tmp_path):
    assert chat.resolve_command(config.load(tmp_path), "grok")[0] == "grok"
