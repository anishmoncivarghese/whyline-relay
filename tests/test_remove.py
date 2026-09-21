import subprocess
from pathlib import Path

import pytest

from whyline_relay import cli, gitcheck, remove


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True
    )
    return tmp_path


def _write_relay(repo: Path, *, state: bool = False) -> Path:
    relay = repo / ".whyline" / "relay"
    (relay / "prompts").mkdir(parents=True)
    (relay / "config.toml").write_text("max_rounds = 3\n")
    (relay / "prompts" / "implement.md").write_text("prompt\n")
    if state:
        (relay / "state.json").write_text("{}\n")
    return relay


def test_nothing_to_remove_is_success(repo: Path, capsys):
    assert remove.run(repo, assume_yes=False, force=False) == 0
    assert (
        "Nothing to remove: whyline-relay is not set up here."
        in capsys.readouterr().out
    )


def test_paused_run_is_refused(repo: Path, capsys):
    relay = _write_relay(repo, state=True)
    assert remove.run(repo, assume_yes=True, force=False) == 1
    assert relay.exists()
    error = capsys.readouterr().err
    assert "whyline-relay resume" in error
    assert "--force" in error


def test_force_removes_a_paused_run(repo: Path):
    relay = _write_relay(repo, state=True)
    assert remove.run(repo, assume_yes=True, force=True) == 0
    assert not relay.exists()


def test_declining_removes_nothing(repo: Path):
    relay = _write_relay(repo)
    gitcheck.ensure_relay_ignored(repo)
    assert (
        remove.run(
            repo, assume_yes=False, force=False, confirm=lambda prompt: "n"
        )
        == 1
    )
    assert relay.exists()
    assert gitcheck.relay_ignore_count(repo) == len(gitcheck.RELAY_IGNORE)


def test_no_terminal_removes_nothing(repo: Path):
    relay = _write_relay(repo)

    def no_terminal(prompt: str) -> str:
        raise EOFError

    assert (
        remove.run(repo, assume_yes=False, force=False, confirm=no_terminal) == 1
    )
    assert relay.exists()


def test_assume_yes_skips_confirmation(repo: Path):
    relay = _write_relay(repo)

    def must_not_ask(prompt: str) -> str:
        raise AssertionError("confirmation was requested")

    assert (
        remove.run(repo, assume_yes=True, force=False, confirm=must_not_ask) == 0
    )
    assert not relay.exists()


def test_only_relay_exclude_lines_are_removed(repo: Path):
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text(
        "# keep this\n"
        "*.swp\n"
        ".whyline/relay/logs/\n"
        ".whyline/relay/state.json*\n"
        ".whyline/relay/STOP\n"
        "build/\n"
    )
    assert remove.run(repo, assume_yes=True, force=False) == 0
    assert exclude.read_text() == "# keep this\n*.swp\nbuild/\n"


def test_tracked_files_are_counted_and_reported(repo: Path, capsys):
    _write_relay(repo)
    subprocess.run(["git", "add", ".whyline/relay"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "relay setup"], cwd=repo, check=True, capture_output=True
    )
    assert remove.run(repo, assume_yes=True, force=False) == 0
    output = capsys.readouterr().out
    assert "Files to remove: 2 (2 tracked by git)." in output
    assert "git status" in output
    assert "need committing" in output


def test_relay_symlink_is_unlinked_without_following(repo: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep\n")
    whyline = repo / ".whyline"
    whyline.mkdir()
    relay = whyline / "relay"
    relay.symlink_to(outside, target_is_directory=True)

    assert remove.run(repo, assume_yes=True, force=False) == 0
    assert not relay.is_symlink()
    assert sentinel.read_text() == "keep\n"


def test_refuses_when_whyline_parent_points_outside_repo(repo: Path, capsys):
    outside = repo.parent / f"{repo.name}-outside-parent"
    (outside / "relay").mkdir(parents=True)
    sentinel = outside / "relay" / "keep.txt"
    sentinel.write_text("keep\n")
    (repo / ".whyline").symlink_to(outside, target_is_directory=True)

    assert remove.run(repo, assume_yes=True, force=True) == 1
    assert sentinel.exists()
    assert "outside" in capsys.readouterr().err


def test_running_twice_is_safe(repo: Path, capsys):
    _write_relay(repo)
    gitcheck.ensure_relay_ignored(repo)
    assert remove.run(repo, assume_yes=True, force=False) == 0
    assert remove.run(repo, assume_yes=True, force=False) == 0
    assert "Nothing to remove" in capsys.readouterr().out


def test_cli_wires_remove_flags(repo: Path):
    relay = _write_relay(repo, state=True)
    assert (
        cli.main(["remove", "--repo", str(repo), "--yes", "--force"])
        == cli.EXIT_OK
    )
    assert not relay.exists()
