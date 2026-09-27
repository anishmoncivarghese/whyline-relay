from pathlib import Path

import pytest

from whyline_relay import chat


def test_load_default_agent_is_none_when_unset(tmp_path: Path):
    assert chat.load_default_agent(tmp_path) is None


def test_save_and_load_default_agent_round_trip(tmp_path: Path):
    chat.save_default_agent(tmp_path, "codex")
    assert chat.load_default_agent(tmp_path) == "codex"


def test_setup_wizard_detects_installed_agents_and_saves_the_choice(tmp_path: Path):
    which = lambda name: f"/bin/{name}" if name in ("claude", "codex") else None
    answers = iter(["codex"])
    chosen = chat.run_setup_wizard(
        tmp_path,
        which=which,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert chosen == "codex"
    assert chat.load_default_agent(tmp_path) == "codex"


def test_setup_wizard_refuses_a_choice_that_is_not_installed(tmp_path: Path):
    which = lambda name: "/bin/claude" if name == "claude" else None
    answers = iter(["grok", "claude"])
    chosen = chat.run_setup_wizard(
        tmp_path,
        which=which,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert chosen == "claude"


def test_setup_wizard_raises_when_nothing_is_installed(tmp_path: Path):
    with pytest.raises(chat.NoAgentsInstalled):
        chat.run_setup_wizard(
            tmp_path,
            which=lambda name: None,
            input_fn=lambda prompt="": "",
            print_fn=lambda *a, **k: None,
        )


def test_setup_wizard_writes_the_relay_gitignore(tmp_path: Path):
    which = lambda name: "/bin/claude" if name == "claude" else None
    chat.run_setup_wizard(
        tmp_path,
        which=which,
        input_fn=lambda prompt="": "claude",
        print_fn=lambda *a, **k: None,
    )
    gitignore = tmp_path / ".whyline" / "relay" / ".gitignore"
    assert "chat.json" in gitignore.read_text()
