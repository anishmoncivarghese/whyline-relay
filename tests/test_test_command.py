import tomllib
from pathlib import Path

import pytest

from whyline_relay import cli, config


def write_config(root: Path, text: str) -> Path:
    path = config.config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["pyproject.toml", "uv.lock"], "uv run pytest -q"),
        (["pyproject.toml"], "pytest -q"),
        (["package.json"], "npm test"),
        ([], ""),
    ],
)
def test_detects_from_the_stack(tmp_path: Path, files: list[str], expected: str):
    for name in files:
        (tmp_path / name).write_text("", encoding="utf-8")

    assert config.test_command(tmp_path) == expected
    assert config.configured_test_command(tmp_path) is None


def test_a_set_value_wins_over_detection(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    write_config(tmp_path, 'test_command = "make check"\n')

    assert config.test_command(tmp_path) == "make check"


def test_a_non_string_value_is_a_config_error(tmp_path: Path):
    write_config(tmp_path, "test_command = 3\n")

    with pytest.raises(config.ConfigError, match="test_command must be a string"):
        config.test_command(tmp_path)


ORIGINAL = """\
# Relay settings
plan = "plan.md"  # the plan file
max_rounds = 3

[roles]
# who does what
implementer = "claude"
reviewer = "codex"
"""


def test_saving_adds_the_key_and_keeps_every_other_line(tmp_path: Path):
    path = write_config(tmp_path, ORIGINAL)

    config.save_test_command(tmp_path, 'pytest -q -k "not slow"')

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    added = [line for line in lines if line.startswith("test_command")]
    assert added == ['test_command = "pytest -q -k \\"not slow\\""']
    assert [line for line in lines if not line.startswith("test_command")] == (
        ORIGINAL.splitlines()
    )
    # The key is top-level, not swallowed by [roles].
    parsed = tomllib.loads(text)
    assert parsed["test_command"] == 'pytest -q -k "not slow"'
    assert parsed["roles"] == {"implementer": "claude", "reviewer": "codex"}
    assert config.test_command(tmp_path) == 'pytest -q -k "not slow"'


def test_saving_replaces_an_existing_value_in_place(tmp_path: Path):
    path = write_config(
        tmp_path, '# top\ntest_command = "old"\nplan = "plan.md"\n\n[roles]\n'
    )

    config.save_test_command(tmp_path, "new")

    assert path.read_text(encoding="utf-8") == (
        '# top\ntest_command = "new"\nplan = "plan.md"\n\n[roles]\n'
    )


def test_saving_into_a_file_with_only_tables_stays_top_level(tmp_path: Path):
    path = write_config(tmp_path, '[roles]\nimplementer = "claude"\n')

    config.save_test_command(tmp_path, "npm test")

    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["test_command"] == "npm test"
    assert parsed["roles"] == {"implementer": "claude"}


def test_saving_creates_the_file_when_missing(tmp_path: Path):
    config.save_test_command(tmp_path, "pytest")

    assert config.config_path(tmp_path).read_text(encoding="utf-8") == (
        'test_command = "pytest"\n'
    )


def test_an_empty_string_removes_the_key(tmp_path: Path):
    path = write_config(tmp_path, ORIGINAL.replace(
        "max_rounds = 3\n", 'max_rounds = 3\ntest_command = "make check"\n'
    ))

    config.save_test_command(tmp_path, "")

    assert path.read_text(encoding="utf-8") == ORIGINAL
    assert config.configured_test_command(tmp_path) is None


def test_removing_leaves_a_same_named_key_in_a_table(tmp_path: Path):
    text = '[agents.x]\ntest_command = "inner"\n'
    path = write_config(tmp_path, text)

    config.save_test_command(tmp_path, "")

    assert path.read_text(encoding="utf-8") == text


def test_removing_with_no_file_does_not_create_one(tmp_path: Path):
    config.save_test_command(tmp_path, "")

    assert not config.config_path(tmp_path).exists()


def test_cli_prints_a_detected_value(tmp_path: Path, capsys):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    assert cli.main(["test-command", "--repo", str(tmp_path)]) == cli.EXIT_OK
    assert capsys.readouterr().out == "npm test (detected)\n"


def test_cli_prints_a_set_value(tmp_path: Path, capsys):
    write_config(tmp_path, 'test_command = "make check"\n')

    assert cli.main(["test-command", "--repo", str(tmp_path)]) == cli.EXIT_OK
    assert capsys.readouterr().out == "make check (set in config.toml)\n"


def test_cli_prints_when_there_is_none(tmp_path: Path, capsys):
    assert cli.main(["test-command", "--repo", str(tmp_path)]) == cli.EXIT_OK
    assert capsys.readouterr().out == "No test command (none set or detected)\n"


@pytest.mark.parametrize("value", ["make check", ""])
def test_cli_saves_through_save_test_command(
    tmp_path: Path, capsys, monkeypatch, value: str
):
    calls = []
    monkeypatch.setattr(
        config, "save_test_command", lambda root, command: calls.append((root, command))
    )

    assert cli.main(["test-command", value, "--repo", str(tmp_path)]) == cli.EXIT_OK
    assert calls == [(tmp_path.resolve(), value)]
    assert capsys.readouterr().out


def test_cli_set_then_show_round_trips(tmp_path: Path, capsys):
    assert cli.main(["test-command", "make check", "--repo", str(tmp_path)]) == 0
    assert cli.main(["test-command", "--repo", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "make check (set in config.toml)"
