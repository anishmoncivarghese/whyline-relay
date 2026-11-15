import json
from pathlib import Path

import pytest

from whyline_relay import config, gitcheck, init, run_settings


RICH_CONFIG = """\
timeout_minutes = 30
plan = "docs/plan.md"
max_rounds = 3
branch_prefix = "work/"

[roles]
implementer = "codex"
reviewer    = "claude"

[planner]
draft = "codex"
review = "grok"
max_visits = 4

[backup]
# used when the primary is out
retries = 2
chain = ["claude"]
"""

THREE_ROLE_GROK = """\
[roles]
implementer = "grok"
tester = "grok"
reviewer = "grok"
[pipeline]
default_profile = "full"
[pipeline.profiles]
full = ["only"]
[pipeline.stages.only]
role = "implementer"
prompt = "implement"
[pipeline.stages.only.on]
ready = "@complete"
"""


def _write_config(root: Path, text: str = RICH_CONFIG) -> config.Config:
    target = config.config_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return config.load(root)


def _file(root: Path) -> Path:
    return run_settings.path(root)


def _non_chain_lines(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if line.split("=", 1)[0].strip() != "chain"
    ]


def test_path_is_under_the_relay_dir(tmp_path: Path):
    assert _file(tmp_path) == tmp_path / ".whyline" / "relay" / "run-settings.json"


def test_missing_file_is_no_changes(tmp_path: Path):
    assert run_settings.read(tmp_path) == run_settings.RunSettings()
    settings = config.load(tmp_path)
    got = run_settings.effective(tmp_path, settings)
    assert got.timeout_minutes == 30
    assert got.max_rounds == 3
    assert got.backup_chain == ()
    assert got.changed == {}
    assert got.problem is None


def test_read_raises_on_bad_json_or_wrong_shape(tmp_path: Path):
    target = _file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{", encoding="utf-8")
    with pytest.raises(run_settings.Unreadable):
        run_settings.read(tmp_path)
    target.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(run_settings.Unreadable):
        run_settings.read(tmp_path)
    target.write_text('{"timeout_minutes": "45"}\n', encoding="utf-8")
    with pytest.raises(run_settings.Unreadable):
        run_settings.read(tmp_path)
    target.write_bytes(b"\xff\xfe not utf-8 {")
    with pytest.raises(run_settings.Unreadable):
        run_settings.read(tmp_path)


def test_effective_unreadable_file_uses_configured_values(tmp_path: Path):
    settings = _write_config(tmp_path)
    _file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _file(tmp_path).write_text("{", encoding="utf-8")
    got = run_settings.effective(tmp_path, settings)
    assert got.timeout_minutes == 30
    assert got.max_rounds == 3
    assert got.backup_chain == ("claude",)
    assert got.changed == {}
    assert got.problem == (
        "run-settings.json can't be read; using the configured values"
    )


def test_effective_non_utf8_file_uses_configured_values(tmp_path: Path):
    settings = _write_config(tmp_path)
    target = _file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\xff\xfe not utf-8 {")
    got = run_settings.effective(tmp_path, settings)
    assert got.timeout_minutes == 30
    assert got.max_rounds == 3
    assert got.backup_chain == ("claude",)
    assert got.changed == {}
    assert got.problem == (
        "run-settings.json can't be read; using the configured values"
    )


def test_set_timeout_message_and_bounds(tmp_path: Path):
    settings = config.load(tmp_path)
    assert run_settings.set_timeout(tmp_path, settings, 45) == (
        "Timeout 45m from the next turn."
    )
    assert run_settings.set_timeout(tmp_path, settings, 0) == (
        "Timeout 0m from the next turn."
    )
    assert run_settings.set_timeout(tmp_path, settings, 1) == (
        "Timeout 1m from the next turn."
    )
    assert run_settings.set_timeout(tmp_path, settings, 1440) == (
        "Timeout 1440m from the next turn."
    )
    record = json.loads(_file(tmp_path).read_text())
    assert record["timeout_minutes"] == 1440
    with pytest.raises(run_settings.RunSettingsError, match="1 to 1440"):
        run_settings.set_timeout(tmp_path, settings, -1)
    with pytest.raises(run_settings.RunSettingsError, match="1 to 1440"):
        run_settings.set_timeout(tmp_path, settings, 1441)
    with pytest.raises(run_settings.RunSettingsError, match="integer"):
        run_settings.set_timeout(tmp_path, settings, True)


def test_set_timeout_equal_to_configured_removes_the_change(tmp_path: Path):
    settings = config.load(tmp_path)
    run_settings.set_timeout(tmp_path, settings, 45)
    run_settings.set_rounds(tmp_path, settings, 5)
    assert run_settings.set_timeout(tmp_path, settings, 30) == (
        "Timeout 30m from the next turn."
    )
    record = json.loads(_file(tmp_path).read_text())
    assert record["timeout_minutes"] is None
    assert record["max_rounds"] == 5
    run_settings.set_rounds(tmp_path, settings, 3)
    assert not _file(tmp_path).exists()


def test_set_rounds_message_and_bounds(tmp_path: Path):
    settings = config.load(tmp_path)
    assert run_settings.set_rounds(tmp_path, settings, 5) == (
        "Round cap 5 from the next check."
    )
    assert run_settings.set_rounds(tmp_path, settings, 1) == (
        "Round cap 1 from the next check."
    )
    assert run_settings.set_rounds(tmp_path, settings, 99) == (
        "Round cap 99 from the next check."
    )
    with pytest.raises(run_settings.RunSettingsError, match="1 to 99"):
        run_settings.set_rounds(tmp_path, settings, 0)
    with pytest.raises(run_settings.RunSettingsError, match="1 to 99"):
        run_settings.set_rounds(tmp_path, settings, 100)


def test_set_rounds_equal_to_configured_removes_the_change(tmp_path: Path):
    settings = config.load(tmp_path)
    run_settings.set_rounds(tmp_path, settings, 5)
    assert run_settings.set_rounds(tmp_path, settings, 3) == (
        "Round cap 3 from the next check."
    )
    assert not _file(tmp_path).exists()


def test_round_cap_below_current_round_is_refused(tmp_path: Path):
    settings = config.load(tmp_path)
    marker = {
        "agent": "codex",
        "task": "LRS-1",
        "round": 4,
        "started": "2026-01-01T00:00:00+00:00",
        "pid": 1,
        "role": "implementer",
        "stage": "",
        "visit": 0,
        "model": "",
    }
    target = tmp_path / ".whyline" / "relay" / "running.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(marker) + "\n", encoding="utf-8")
    with pytest.raises(run_settings.RunSettingsError) as raised:
        run_settings.set_rounds(tmp_path, settings, 3, current_round=4)
    assert str(raised.value) == (
        "LRS-1 is already on round 4; the cap can't go below that"
    )
    assert not _file(tmp_path).exists()
    assert run_settings.set_rounds(tmp_path, settings, 4, current_round=4) == (
        "Round cap 4 from the next check."
    )


def test_set_backup_message(tmp_path: Path):
    settings = config.load(tmp_path)
    assert run_settings.set_backup(tmp_path, settings, ["codex", "grok"]) == (
        "Backups: codex, then grok, from the next time one is needed."
    )
    assert run_settings.set_backup(tmp_path, settings, ["codex"]) == (
        "Backups: codex, from the next time one is needed."
    )
    assert run_settings.set_backup(tmp_path, settings, []) == (
        "Backups: none, from the next time one is needed."
    )
    # Default chain is already empty, so equal-to-configured drops the file.
    assert not _file(tmp_path).exists()
    settings = _write_config(tmp_path)
    assert run_settings.set_backup(tmp_path, settings, []) == (
        "Backups: none, from the next time one is needed."
    )
    assert json.loads(_file(tmp_path).read_text())["backup_chain"] == []


def test_set_backup_equal_to_configured_removes_the_change(tmp_path: Path):
    settings = _write_config(tmp_path)
    run_settings.set_backup(tmp_path, settings, ["codex", "grok"])
    assert run_settings.set_backup(tmp_path, settings, ["claude"]) == (
        "Backups: claude, from the next time one is needed."
    )
    assert not _file(tmp_path).exists()


def test_backup_order_is_validated(tmp_path: Path):
    settings = config.load(tmp_path)
    with pytest.raises(run_settings.RunSettingsError, match="not set up"):
        run_settings.set_backup(tmp_path, settings, ["nobody"])
    with pytest.raises(run_settings.RunSettingsError, match="listed twice"):
        run_settings.set_backup(tmp_path, settings, ["codex", "codex"])
    three = _write_config(tmp_path, THREE_ROLE_GROK)
    with pytest.raises(run_settings.RunSettingsError) as raised:
        run_settings.set_backup(tmp_path, three, ["grok"])
    assert str(raised.value) == (
        "grok is the configured agent of all three roles"
    )
    with pytest.raises(run_settings.RunSettingsError, match="all three roles"):
        run_settings.set_backup(tmp_path, three, ["codex", "grok"])
    assert run_settings.set_backup(tmp_path, three, ["codex"]) == (
        "Backups: codex, from the next time one is needed."
    )
    assert json.loads(_file(tmp_path).read_text())["backup_chain"] == ["codex"]


def test_without_keep_nothing_lasts(tmp_path: Path):
    settings = _write_config(tmp_path)
    before = config.config_path(tmp_path).read_bytes()
    run_settings.set_timeout(tmp_path, settings, 45)
    run_settings.set_rounds(tmp_path, settings, 5)
    run_settings.set_backup(tmp_path, settings, ["codex", "grok"])
    assert config.config_path(tmp_path).read_bytes() == before
    assert _file(tmp_path).exists()
    run_settings.clear(tmp_path, completed=False)
    assert _file(tmp_path).exists()
    run_settings.clear(tmp_path, completed=True)
    assert not _file(tmp_path).exists()
    assert config.config_path(tmp_path).read_bytes() == before


def test_keep_changes_only_its_own_line(tmp_path: Path):
    settings = _write_config(tmp_path)
    target = config.config_path(tmp_path)
    before = target.read_text(encoding="utf-8")

    run_settings.set_timeout(tmp_path, settings, 45, keep=True)
    after_timeout = target.read_text(encoding="utf-8")
    assert after_timeout == before.replace(
        "timeout_minutes = 30\n", "timeout_minutes = 45\n"
    )
    assert run_settings.read(tmp_path).keep == ("timeout_minutes",)

    run_settings.set_rounds(tmp_path, settings, 5, keep=True)
    after_rounds = target.read_text(encoding="utf-8")
    assert after_rounds == after_timeout.replace(
        "max_rounds = 3\n", "max_rounds = 5\n"
    )
    assert "timeout_minutes = 45\n" in after_rounds
    assert 'plan = "docs/plan.md"' in after_rounds
    assert 'review = "grok"' in after_rounds

    run_settings.set_backup(tmp_path, settings, ["codex", "grok"], keep=True)
    after_backup = target.read_text(encoding="utf-8")
    assert "timeout_minutes = 45\n" in after_backup
    assert "max_rounds = 5\n" in after_backup
    assert 'reviewer    = "claude"' in after_backup
    assert "[planner]" in after_backup
    assert "# used when the primary is out" in after_backup
    assert "retries = 2" in after_backup
    assert 'chain = ["codex", "grok"]' in after_backup
    assert _non_chain_lines(after_backup) == _non_chain_lines(after_rounds)
    assert config.load(tmp_path).backup_chain == ["codex", "grok"]

    run_settings.set_backup(tmp_path, settings, [], keep=True)
    after_empty = target.read_text(encoding="utf-8")
    assert "# used when the primary is out" in after_empty
    assert "retries = 2" in after_empty
    assert "[backup]" in after_empty
    assert _non_chain_lines(after_empty) == _non_chain_lines(after_backup)
    assert all(
        line.split("=", 1)[0].strip() != "chain" for line in after_empty.splitlines()
    )
    assert config.load(tmp_path).backup_chain == []


def test_reset_one_and_all(tmp_path: Path):
    settings = config.load(tmp_path)
    run_settings.set_timeout(tmp_path, settings, 45)
    run_settings.set_rounds(tmp_path, settings, 5)
    run_settings.set_backup(tmp_path, settings, ["codex"])
    assert run_settings.reset(tmp_path, "timeout") == "Timeout reset."
    record = json.loads(_file(tmp_path).read_text())
    assert record["timeout_minutes"] is None
    assert record["max_rounds"] == 5
    assert record["backup_chain"] == ["codex"]
    assert run_settings.reset(tmp_path, "rounds") == "Round cap reset."
    assert json.loads(_file(tmp_path).read_text())["max_rounds"] is None
    assert run_settings.reset(tmp_path, "backup") == "Backups reset."
    assert not _file(tmp_path).exists()
    assert run_settings.reset(tmp_path, "timeout") == "Nothing to reset."

    run_settings.set_timeout(tmp_path, settings, 45)
    run_settings.set_rounds(tmp_path, settings, 5)
    assert run_settings.reset(tmp_path) == (
        "Timeout, round cap and backups reset."
    )
    assert not _file(tmp_path).exists()
    assert run_settings.reset(tmp_path) == "Nothing to reset."


def test_run_settings_is_a_runtime_gitignore_line():
    assert "run-settings.json" in gitcheck.RELAY_RUNTIME_FILES
    assert ".whyline/relay/run-settings.json" in gitcheck.RELAY_IGNORE
    assert "run-settings.json" in init.RELAY_GITIGNORE_LINES


def test_effective_reports_each_changed_value(tmp_path: Path):
    settings = _write_config(tmp_path)
    run_settings.set_timeout(tmp_path, settings, 45)
    run_settings.set_rounds(tmp_path, settings, 5)
    run_settings.set_backup(tmp_path, settings, ["codex", "grok"])
    got = run_settings.effective(tmp_path, settings)
    assert got.timeout_minutes == 45
    assert got.max_rounds == 5
    assert got.backup_chain == ("codex", "grok")
    assert got.changed == {
        "timeout_minutes": (30, 45),
        "max_rounds": (3, 5),
        "backup_chain": (("claude",), ("codex", "grok")),
    }
    assert got.problem is None
    assert not list(_file(tmp_path).parent.glob("*.tmp"))
