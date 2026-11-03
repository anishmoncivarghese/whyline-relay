import json
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


def test_grok_recipe_tells_grok_to_stay_inside_its_allow_list():
    # Headless grok cancels its whole turn on any command outside the allow
    # list (measured on grok 1.0.41), so the recipe carries --rules saying so.
    command = list(recipes.RECIPES["grok"])
    rules = command[command.index("--rules") + 1]
    assert "ends your whole turn" in rules
    assert "if, for, while" in rules
    assert command.index("--rules") < command.index("-p")


ANTIGRAVITY_EXPECTED = [
    "read_file(*)", "write_file(*)", "edit_file(*)",
    *(f"command({prefix})" for prefix in (
        "git add", "git commit", "git diff", "git status", "git log", "git show",
        "git rev-parse", "whyline", "python3", "uv run", "uv", "mkdir", "ls",
        "find", "touch", "cat", "grep", "head", "tail", "wc", "diff", "pwd",
        "pytest", ".venv/bin/pytest", ".venv/bin/python",
    )),
]


def _antigravity_allow() -> list[str]:
    command = list(recipes.RECIPES["antigravity"])
    settings = command[command.index("--settings") + 1]
    return json.loads(settings)["permissions"]["allow"]


def test_antigravity_recipe_passes_settings_right_before_the_prompt_flag():
    command = list(recipes.RECIPES["antigravity"])
    assert command[-1] == "-p"
    assert command[-3] == "--settings"
    assert json.loads(command[-2]) == {"permissions": {"allow": ANTIGRAVITY_EXPECTED}}


def test_antigravity_allow_list_is_exactly_grok_commands_plus_file_tools():
    assert _antigravity_allow() == ANTIGRAVITY_EXPECTED
    assert list(recipes.ANTIGRAVITY_ALLOW) == ANTIGRAVITY_EXPECTED
    grok = list(recipes.RECIPES["grok"])
    grok_bash = {
        grok[i + 1][len("Bash("):-len(":*)")]
        for i, arg in enumerate(grok[:-1])
        if arg == "--allow" and grok[i + 1].startswith("Bash(")
    }
    agy_commands = {
        rule[len("command("):-1] for rule in _antigravity_allow()
        if rule.startswith("command(")
    }
    assert agy_commands == grok_bash


def test_antigravity_allow_list_has_no_wildcard_push_or_rm():
    allow = _antigravity_allow()
    assert "command(*)" not in allow
    for rule in allow:
        assert "push" not in rule
        assert not rule.startswith("command(rm")


def test_a_repos_own_antigravity_table_is_used_unchanged(tmp_path):
    _write(
        tmp_path,
        '[agents.antigravity]\nadapter = "generic"\ncommand = ["agy", "--mode", "x", "-p"]\n',
    )
    settings = config.load(tmp_path)
    assert settings.agents["antigravity"] == ["agy", "--mode", "x", "-p"]
    assert chat.resolve_command(settings, "antigravity") == ["agy", "--mode", "x", "-p"]


def test_grok_recipe_allows_the_read_only_commands_agents_reach_for():
    command = list(recipes.RECIPES["grok"])
    allowed = {command[i + 1] for i, arg in enumerate(command) if arg == "--allow"}
    for rule in ("Bash(grep:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)",
                 "Bash(git show:*)", "Bash(pytest:*)", "WebFetch"):
        assert rule in allowed
