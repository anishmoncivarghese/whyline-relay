"""`whyline-relay settings`: live timeout, round cap and backup changes."""

import json
import os
from datetime import datetime
from pathlib import Path

from whyline_relay import cli, config, run_settings


CONFIG = (
    "timeout_minutes = 30\n"
    "max_rounds = 3\n"
    '[roles]\nimplementer = "codex"\nreviewer = "claude"\n'
)


def make_repo(tmp_path: Path) -> Path:
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


def write_running(root: Path, *, task: str = "LRS-3", round: int = 2) -> None:
    target = root / ".whyline" / "relay" / "running.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "agent": "codex",
                "task": task,
                "round": round,
                "started": datetime.now().astimezone().isoformat(),
                "pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )


def test_settings_lists_the_configured_values(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert cli.main(["settings", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == "timeout 30m\nrounds 3\nbackup none\n"


def test_settings_lists_a_changed_timeout(tmp_path, capsys):
    repo = make_repo(tmp_path)
    run_settings.set_timeout(repo, config.load(repo), 45)
    assert cli.main(["settings", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == (
        "timeout 45m (was 30m, changed by you)\nrounds 3\nbackup none\n"
    )


def test_settings_lists_each_changed_value(tmp_path, capsys):
    repo = make_repo(tmp_path)
    settings = config.load(repo)
    run_settings.set_timeout(repo, settings, 45)
    run_settings.set_rounds(repo, settings, 5)
    run_settings.set_backup(repo, settings, ["codex", "grok"])
    assert cli.main(["settings", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == (
        "timeout 45m (was 30m, changed by you)\n"
        "rounds 5 (was 3, changed by you)\n"
        "backup codex, then grok (was none, changed by you)\n"
    )


def test_settings_timeout_prints_the_message(tmp_path, capsys):
    repo = make_repo(tmp_path)
    write_running(repo)
    assert cli.main(["settings", "timeout", "45", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == "Timeout 45m from the next turn.\n"
    assert (repo / ".whyline" / "relay" / "config.toml").read_text(
        encoding="utf-8"
    ) == CONFIG


def test_settings_timeout_keep_writes_config(tmp_path, capsys):
    repo = make_repo(tmp_path)
    write_running(repo)
    assert (
        cli.main(["settings", "timeout", "45", "--keep", "--repo", str(repo)])
        == 0
    )
    assert capsys.readouterr().out == "Timeout 45m from the next turn.\n"
    assert "timeout_minutes = 45" in (
        repo / ".whyline" / "relay" / "config.toml"
    ).read_text(encoding="utf-8")


def test_settings_rounds_prints_the_message(tmp_path, capsys):
    repo = make_repo(tmp_path)
    write_running(repo)
    assert cli.main(["settings", "rounds", "5", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == "Round cap 5 from the next check.\n"


def test_settings_rounds_passes_the_running_round(tmp_path, capsys):
    repo = make_repo(tmp_path)
    write_running(repo, task="LRS-3", round=4)
    assert cli.main(["settings", "rounds", "3", "--repo", str(repo)]) == 2
    assert capsys.readouterr().err == (
        "Error: LRS-3 is already on round 4; the cap can't go below that\n"
    )


def test_settings_backup_prints_the_message(tmp_path, capsys):
    repo = make_repo(tmp_path)
    write_running(repo)
    assert (
        cli.main(["settings", "backup", "codex", "grok", "--repo", str(repo)])
        == 0
    )
    assert capsys.readouterr().out == (
        "Backups: codex, then grok, from the next time one is needed.\n"
    )


def test_settings_backup_with_no_agents_clears_the_chain(tmp_path, capsys):
    repo = make_repo(tmp_path)
    write_running(repo)
    settings = config.load(repo)
    run_settings.set_backup(repo, settings, ["codex"])
    assert cli.main(["settings", "backup", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == (
        "Backups: none, from the next time one is needed.\n"
    )


def test_settings_reset_one_and_all(tmp_path, capsys):
    repo = make_repo(tmp_path)
    write_running(repo)
    settings = config.load(repo)
    run_settings.set_timeout(repo, settings, 45)
    run_settings.set_rounds(repo, settings, 5)
    assert cli.main(["settings", "reset", "timeout", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == "Timeout reset.\n"
    assert cli.main(["settings", "reset", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == "Timeout, round cap and backups reset.\n"


def test_settings_bad_timeout_exits_2(tmp_path, capsys):
    repo = make_repo(tmp_path)
    write_running(repo)
    assert cli.main(["settings", "timeout", "1441", "--repo", str(repo)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ")
    assert "1 to 1440" in err


def test_settings_with_no_relay_implies_keep(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert cli.main(["settings", "timeout", "45", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out == (
        "Timeout 45m from the next turn. Saved for the next run.\n"
    )
    text = (repo / ".whyline" / "relay" / "config.toml").read_text(
        encoding="utf-8"
    )
    assert "timeout_minutes = 45" in text
