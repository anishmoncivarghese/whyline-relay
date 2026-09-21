import json
import os
from pathlib import Path

from whyline_relay import cli, init


GENERATED = (
    ".whyline/relay/claude-settings.json",
    ".whyline/relay/prompts/implement.md",
    ".whyline/relay/prompts/review.md",
    ".whyline/relay/config.toml",
    ".whyline/relay/.gitignore",
)


def generated_paths(root: Path) -> list[Path]:
    return [root / relative for relative in GENERATED]


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
    assert "Bash(.venv/bin/pytest:*)" in allowed
    assert "Bash(.venv/bin/python:*)" in allowed


def test_base_allowlist_allows_read_only_shell_helpers():
    allowed = init.allowlist("base")["permissions"]["allow"]
    assert "Bash(tail:*)" in allowed
    assert "Bash(head:*)" in allowed
    assert "Bash(wc:*)" in allowed
    assert "Bash(grep:*)" in allowed
    assert "Bash(ls:*)" in allowed


def test_node_preset_allows_npm_test():
    assert "Bash(npm test:*)" in init.allowlist("node")["permissions"]["allow"]


def test_run_writes_settings_templates_and_config(tmp_path: Path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert init.run(tmp_path, assume_yes=True) == 0
    out = capsys.readouterr().out
    settings = json.loads(
        (tmp_path / ".whyline" / "relay" / "claude-settings.json").read_text()
    )
    assert "Bash(whyline:*)" in settings["permissions"]["allow"]
    assert (tmp_path / ".whyline" / "relay" / "prompts" / "implement.md").exists()
    assert (tmp_path / ".whyline" / "relay" / "prompts" / "review.md").exists()
    assert (tmp_path / ".whyline" / "relay" / "config.toml").exists()
    assert "running.json" in (
        tmp_path / ".whyline" / "relay" / ".gitignore"
    ).read_text().splitlines()
    for relative in GENERATED:
        assert f"Wrote {relative}." in out


def test_second_run_with_no_edits_does_not_touch_or_report_files(
    tmp_path: Path, capsys
):
    init.run(tmp_path, assume_yes=True)
    capsys.readouterr()
    old_timestamp = 1_000_000_000
    for path in generated_paths(tmp_path):
        os.utime(path, ns=(old_timestamp, old_timestamp))

    assert init.run(tmp_path, assume_yes=True) == 0

    out = capsys.readouterr().out
    assert "Kept " not in out
    assert "Wrote " not in out
    assert all(
        path.stat().st_mtime_ns == old_timestamp
        for path in generated_paths(tmp_path)
    )


def test_an_edited_config_is_kept_and_reported(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    capsys.readouterr()
    config_path = tmp_path / ".whyline" / "relay" / "config.toml"
    config_path.write_text("edited config\n")

    assert init.run(tmp_path, assume_yes=True) == 0

    assert config_path.read_text() == "edited config\n"
    assert capsys.readouterr().out.count(
        "Kept .whyline/relay/config.toml: it already exists and differs. "
        "Run with --overwrite to replace it.\n"
    ) == 1


def test_edited_prompt_and_permissions_are_kept_and_reported(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    capsys.readouterr()
    edited = {
        tmp_path / ".whyline" / "relay" / "prompts" / "implement.md": "my prompt\n",
        tmp_path / ".whyline" / "relay" / "claude-settings.json": "my permissions\n",
    }
    for path, content in edited.items():
        path.write_text(content)

    assert init.run(tmp_path, assume_yes=True) == 0

    out = capsys.readouterr().out
    for path, content in edited.items():
        assert path.read_text() == content
        relative = path.relative_to(tmp_path)
        assert (
            f"Kept {relative}: it already exists and differs. "
            "Run with --overwrite to replace it."
        ) in out


def test_overwrite_restores_edits_and_rewrites_every_file(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    defaults = {path: path.read_bytes() for path in generated_paths(tmp_path)}
    capsys.readouterr()
    for path in generated_paths(tmp_path)[:3]:
        path.write_text("edited\n")
    old_timestamp = 1_000_000_000
    for path in generated_paths(tmp_path):
        os.utime(path, ns=(old_timestamp, old_timestamp))

    assert (
        cli.main(["init", "--repo", str(tmp_path), "--yes", "--overwrite"])
        == cli.EXIT_OK
    )

    out = capsys.readouterr().out
    for path, content in defaults.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns != old_timestamp
        assert f"Wrote {path.relative_to(tmp_path)}." in out
    assert "Kept " not in out


def test_a_missing_file_is_created_among_existing_files(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    capsys.readouterr()
    missing = tmp_path / ".whyline" / "relay" / "prompts" / "review.md"
    missing.unlink()

    assert init.run(tmp_path, assume_yes=True) == 0

    assert missing.read_text() == init.prompts.REVIEW
    assert f"Wrote {missing.relative_to(tmp_path)}." in capsys.readouterr().out


def test_init_overwrite_flag_has_the_required_help():
    parser = cli.build_parser()
    init_parser = next(
        action for action in parser._actions if action.dest == "command"
    ).choices["init"]
    overwrite = next(
        action for action in init_parser._actions if action.dest == "overwrite"
    )

    assert overwrite.help == "Replace files that already exist, discarding your edits."


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
