"""Antigravity's agent name is `antigravity` (the key the README's recipe
and preflight use); `agy` is only its executable. Chat and brainstorm used
to use `agy` as the agent name too, so in a repo configured the documented
way `/agy`, `/default agy` and brainstorm's Antigravity option all failed
with "agy is not configured for chat in this repo"."""

import json
from pathlib import Path

from whyline_relay import brainstorm, chat, chatlog, config

ANTIGRAVITY_TOML = (
    '[agents.antigravity]\nadapter = "generic"\n'
    'command = ["agy", "--output-format", "json", "-p"]\n'
)
LEGACY_TOML = '[agents.agy]\nadapter = "generic"\ncommand = ["agy", "--output-format", "json", "-p"]\n'


def _repo(tmp_path: Path, toml: str = ANTIGRAVITY_TOML) -> Path:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(toml, encoding="utf-8")
    (tmp_path / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _agy_run_fn(command, prompt, **kwargs):
    from whyline_relay.agents import RunResult

    return RunResult(0, json.dumps({"status": "SUCCESS", "response": "from agy"}))


def test_brainstorm_offers_antigravity_under_its_agent_name():
    assert brainstorm.parse_model_selection("3") == [("antigravity", "Antigravity")]
    assert brainstorm.MODEL_OPTIONS_BY_KEY["antigravity"] == "Antigravity"


def test_brainstorm_antigravity_is_available_with_the_documented_config(tmp_path):
    settings = config.load(_repo(tmp_path))
    models = brainstorm.parse_model_selection("3")
    assert brainstorm.check_availability(settings, models) == []


def test_agy_is_accepted_as_a_spelling_of_antigravity(tmp_path):
    settings = config.load(_repo(tmp_path))
    assert chat.resolve_command(settings, "agy")[0] == "agy"
    assert chat.resolve_command(settings, "antigravity")[0] == "agy"


def test_a_legacy_agents_agy_config_still_resolves_as_antigravity(tmp_path):
    settings = config.load(_repo(tmp_path, LEGACY_TOML))
    assert chat.resolve_command(settings, "antigravity")[0] == "agy"
    assert chat.resolve_command(settings, "agy")[0] == "agy"


def test_slash_agy_and_slash_antigravity_both_reach_antigravity(tmp_path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    lines = iter(["/agy hello", "/antigravity again", "/exit"])
    printed = []
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=_agy_run_fn,
        which=lambda name: f"/bin/{name}",
    )
    assert not any("not configured" in line for line in printed)
    assert [turn["agent"] for turn in chatlog.load(root)] == ["antigravity", "antigravity"]


def test_default_agy_is_saved_as_antigravity(tmp_path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "claude")
    lines = iter(["/default agy", "/exit"])
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: None,
        run_fn=_agy_run_fn,
        which=lambda name: f"/bin/{name}",
    )
    assert chat.load_default_agent(root) == "antigravity"


def test_a_saved_default_of_agy_still_works(tmp_path):
    root = _repo(tmp_path)
    chat.save_default_agent(root, "agy")  # written by an older relay
    lines = iter(["hello", "/exit"])
    chat.repl(
        root,
        input_fn=lambda prompt="": next(lines),
        print_fn=lambda *a, **k: None,
        run_fn=_agy_run_fn,
        which=lambda name: f"/bin/{name}",
    )
    assert chatlog.load(root)[0]["agent"] == "antigravity"


def test_setup_wizard_and_agents_list_find_antigravity_by_its_executable(tmp_path):
    root = _repo(tmp_path)
    which = lambda name: f"/bin/{name}" if name == "agy" else None
    printed = []
    chosen = chat.run_setup_wizard(
        root, which=which, input_fn=lambda prompt="": "",
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    assert chosen == "antigravity"
    assert any("Detected: antigravity" in line for line in printed)
    lines = chat._agent_status_lines(config.load(root), which)
    assert "antigravity: installed, configured" in lines
