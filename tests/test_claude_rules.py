"""Headless claude is told its limits and the one test command it may run,
through --append-system-prompt, unless the repository passes its own."""

import json
from pathlib import Path

import pytest

from whyline_relay import agents, config, recipes
from whyline_relay.adapters import claude


def write(root: Path, text: str) -> None:
    path = config.config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rules_of(command: list[str]) -> str:
    assert command.count("--append-system-prompt") == 1
    return command[command.index("--append-system-prompt") + 1]


def test_the_rules_are_the_headless_rules_plus_the_test_line():
    assert recipes.CLAUDE_RULES == recipes.GROK_RULES
    assert claude.system_prompt("make check") == (
        f"{recipes.GROK_RULES} Run tests only with `make check`."
    )


def test_an_empty_test_command_leaves_the_test_line_out():
    assert claude.system_prompt("") == recipes.GROK_RULES
    assert "Run tests" not in claude.system_prompt("")


def test_the_detected_test_command_is_added(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    loaded = config.load(tmp_path)
    command = loaded.agents["claude"]
    assert command[: len(config.DEFAULTS["agents"]["claude"])] == (
        config.DEFAULTS["agents"]["claude"]
    )
    assert command[-2:] == ["--append-system-prompt", claude.system_prompt("uv run pytest -q")]
    assert rules_of(command).endswith("Run tests only with `uv run pytest -q`.")


def test_the_configured_test_command_wins_over_detection(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    write(tmp_path, 'test_command = "make check"\n')
    command = config.load(tmp_path).agents["claude"]
    assert rules_of(command).endswith("Run tests only with `make check`.")
    assert "npm test" not in rules_of(command)


def test_no_test_command_gives_the_rules_without_the_line(tmp_path: Path):
    command = config.load(tmp_path).agents["claude"]
    assert rules_of(command) == recipes.CLAUDE_RULES


def test_a_claude_variant_with_a_model_gets_the_rules_after_the_model(tmp_path: Path):
    write(tmp_path, '[agents.claude-opus]\nadapter = "claude"\nmodel = "opus"\n')
    command = config.load(tmp_path).agents["claude-opus"]
    assert command[-4:] == [
        "--model", "opus", "--append-system-prompt", recipes.CLAUDE_RULES
    ]


@pytest.mark.parametrize(
    "own",
    [
        ["--append-system-prompt", "repo text"],
        ["--append-system-prompt-file", "rules.md"],
        ["--append-system-prompt=repo text"],
    ],
)
def test_a_configured_flag_is_not_doubled(tmp_path: Path, own: list[str]):
    configured = ["claude", "-p", *own]
    write(tmp_path, f"[agents.claude]\ncommand = {json.dumps(configured)}\n")
    command = config.load(tmp_path).agents["claude"]
    assert command == configured


def test_the_prompt_is_still_the_last_argument(tmp_path: Path):
    write(tmp_path, 'test_command = "make check"\n[agents.claude]\nmodel = "haiku"\n')
    command = config.load(tmp_path).agents["claude"]
    argv = agents.build_argv(command, "PROMPT")
    assert argv[-1] == "PROMPT"
    assert argv[-3:-1] == ["--append-system-prompt", claude.system_prompt("make check")]


def test_codex_and_generic_agents_are_unchanged(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    write(
        tmp_path,
        '[agents.codex-fast]\nadapter = "codex"\n'
        '[agents.mine]\nadapter = "generic"\ncommand = ["mine", "-p"]\n',
    )
    loaded = config.load(tmp_path)
    assert loaded.agents["codex"] == config.DEFAULTS["agents"]["codex"]
    assert loaded.agents["codex-fast"] == config.DEFAULTS["agents"]["codex"]
    assert loaded.agents["mine"] == ["mine", "-p"]
    for name, recipe in recipes.RECIPES.items():
        assert loaded.agents[name] == list(recipe)
    for name in ("codex", "codex-fast", "mine", *recipes.RECIPES):
        assert not any("append-system-prompt" in part for part in loaded.agents[name])
