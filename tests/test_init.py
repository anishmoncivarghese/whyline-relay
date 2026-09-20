import json
from pathlib import Path

from whyline_relay import init


def test_detects_python_from_pyproject(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert init.detect_stack(tmp_path) == "python"


def test_detects_node_from_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    assert init.detect_stack(tmp_path) == "node"


def test_unknown_stack_falls_back_to_base(tmp_path: Path):
    assert init.detect_stack(tmp_path) == "base"


def test_allowlist_denies_push_and_recursive_delete():
    built = init.allowlist("python")
    assert "Bash(git push:*)" in built["permissions"]["deny"]
    assert "Bash(rm -rf:*)" in built["permissions"]["deny"]


def test_allowlist_never_contains_a_bypass_flag():
    for stack in ("python", "node", "base"):
        assert "dangerously" not in json.dumps(init.allowlist(stack))


def test_python_preset_allows_pytest():
    allowed = init.allowlist("python")["permissions"]["allow"]
    assert "Bash(pytest:*)" in allowed
    assert "Bash(uv run pytest:*)" in allowed


def test_node_preset_allows_npm_test():
    assert "Bash(npm test:*)" in init.allowlist("node")["permissions"]["allow"]


def test_run_writes_settings_templates_and_config(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert init.run(tmp_path, assume_yes=True) == 0
    settings = json.loads(
        (tmp_path / ".whyline" / "relay" / "claude-settings.json").read_text()
    )
    assert "Bash(whyline:*)" in settings["permissions"]["allow"]
    assert (tmp_path / ".whyline" / "relay" / "prompts" / "implement.md").exists()
    assert (tmp_path / ".whyline" / "relay" / "prompts" / "review.md").exists()
    assert (tmp_path / ".whyline" / "relay" / "config.toml").exists()


def test_declining_writes_nothing(tmp_path: Path):
    assert init.run(tmp_path, assume_yes=False, confirm=lambda prompt: "n") != 0
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".whyline" / "relay").exists()


def test_says_codex_hooks_are_not_needed(tmp_path: Path, capsys):
    (tmp_path / ".whyline").mkdir()
    init.run(tmp_path, assume_yes=True)
    out = capsys.readouterr().out
    assert "codex" in out.lower()
    assert "whyline sync" in out
    assert "dangerously" not in out


def test_no_terminal_means_nothing_is_written(tmp_path: Path):
    def no_terminal(prompt):
        raise EOFError

    assert init.run(tmp_path, assume_yes=False, confirm=no_terminal) != 0
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".whyline" / "relay").exists()


def test_says_the_allowlist_is_not_a_security_boundary(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    out = capsys.readouterr().out
    assert "not a sandbox" in out
    assert "no secrets" in out


def test_the_permissions_file_is_the_relays_own_and_leaves_claude_settings_alone(
    tmp_path: Path,
):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"model": "opus"}')
    init.run(tmp_path, assume_yes=True)
    assert json.loads((tmp_path / ".claude" / "settings.json").read_text()) == {"model": "opus"}
    command = json.loads(
        (tmp_path / ".whyline" / "relay" / "claude-settings.json").read_text()
    )
    assert "Bash(git commit:*)" in command["permissions"]["allow"]
