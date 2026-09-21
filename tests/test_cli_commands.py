import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from whyline_relay import cli, gitcheck, state

FAKE = str(Path(__file__).parent / "fake_agent.py")


def test_every_subcommand_option_has_help():
    parser = cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    missing = [
        f"{command} {'/'.join(action.option_strings)}"
        for command, subparser in subparsers.choices.items()
        for action in subparser._actions
        if action.option_strings
        and "--help" not in action.option_strings
        and (not isinstance(action.help, str) or not action.help.strip())
    ]

    assert missing == []


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-m", "initial")
    (tmp_path / ".whyline").mkdir()
    (tmp_path / "plan.md").write_text("- [ ] WL-1: One\n")
    return tmp_path


def test_refuses_to_run_on_main(repo: Path, capsys):
    code = cli.main(["start", "--repo", str(repo), "--branch", "main", "--allow-dirty"])
    assert code == cli.EXIT_ERROR
    assert "main" in capsys.readouterr().err


def test_allow_main_lifts_the_branch_refusal(repo: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli.loop, "run_plan", lambda *a, **k: [])
    code = cli.main(
        ["start", "--repo", str(repo), "--branch", "main", "--allow-main", "--allow-dirty"]
    )
    assert code == cli.EXIT_OK


def test_starting_from_main_switches_to_the_relay_branch(repo: Path, monkeypatch, capsys):
    """The guard is about where the agents will commit, not where you started."""
    monkeypatch.setattr(cli.loop, "run_plan", lambda *a, **k: [])
    code = cli.main(["start", "--repo", str(repo), "--allow-dirty"])
    assert code == cli.EXIT_OK, capsys.readouterr().err
    assert gitcheck.current_branch(repo) == "relay/plan"


def test_the_branch_flag_names_the_branch_to_use(repo: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli.loop, "run_plan", lambda *a, **k: [])
    code = cli.main(["start", "--repo", str(repo), "--branch", "feature/x", "--allow-dirty"])
    assert code == cli.EXIT_OK, capsys.readouterr().err
    assert gitcheck.current_branch(repo) == "feature/x"


def test_an_unknown_only_id_is_an_error(repo: Path, capsys):
    code = cli.main(["start", "--repo", str(repo), "--only", "WL-99", "--allow-dirty"])
    assert code == cli.EXIT_ERROR
    assert "WL-99" in capsys.readouterr().err


def test_a_malformed_plan_is_a_clear_error(repo: Path, capsys):
    (repo / "plan.md").write_text("- [ ] WL-1: a\n- [ ] WL-1: b\n")
    code = cli.main(["start", "--repo", str(repo), "--allow-dirty"])
    assert code == cli.EXIT_ERROR
    assert "duplicate" in capsys.readouterr().err


def test_refuses_a_dirty_tree(repo: Path, capsys):
    (repo / "scratch.txt").write_text("x")
    code = cli.main(["start", "--repo", str(repo), "--allow-main"])
    assert code == cli.EXIT_ERROR
    assert "uncommitted" in capsys.readouterr().err.lower()


def test_stop_writes_the_stop_file(repo: Path):
    assert cli.main(["stop", "--repo", str(repo)]) == cli.EXIT_OK
    assert (repo / ".whyline" / "relay" / "STOP").exists()


def test_status_reports_nothing_in_progress(repo: Path, capsys):
    assert cli.main(["status", "--repo", str(repo)]) == cli.EXIT_OK
    assert "no relay run in progress" in capsys.readouterr().out.lower()


def test_status_reports_the_saved_pause(repo: Path, capsys):
    state.save(
        repo,
        state.RelayState(
            plan="plan.md",
            branch="relay/plan",
            task_id="WL-1",
            round=2,
            base_commit="abc",
            paused_reason="codex exited without handing off",
            log_path="/tmp/x.log",
        ),
    )
    cli.main(["status", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert "WL-1" in out
    assert "without handing off" in out


def test_resume_without_saved_state_is_an_error(repo: Path, capsys):
    assert cli.main(["resume", "--repo", str(repo)]) == cli.EXIT_ERROR
    assert "nothing to resume" in capsys.readouterr().err.lower()


def test_resume_clears_the_stop_file_before_continuing(repo: Path, monkeypatch):
    relay = repo / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "STOP").write_text("")
    state.save(
        repo,
        state.RelayState(
            plan=str(repo / "plan.md"),
            branch="relay/plan",
            task_id="WL-1",
            round=1,
            base_commit="abc",
            paused_reason="paused",
            log_path="",
        ),
    )
    monkeypatch.setattr(cli.loop, "run_plan", lambda *a, **k: [])
    assert cli.main(["resume", "--repo", str(repo)]) == cli.EXIT_OK
    assert not (relay / "STOP").exists()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_resume_reuses_the_saved_base_commit(repo: Path, monkeypatch, capsys):
    relay = repo / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "config.toml").write_text(
        f'[agents.codex]\ncommand = ["{sys.executable}", "{FAKE}", "review", "{repo}"]\n'
        f'[agents.claude]\ncommand = ["{sys.executable}", "{FAKE}", "approve", "{repo}"]\n'
    )
    monkeypatch.setattr(cli.loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")
    monkeypatch.setattr(cli.loop.whylinecmd, "claim", lambda *a, **k: None)
    (repo / ".whyline" / ".gitignore").write_text("active-handoff.json\nledger.jsonl\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "relay setup")
    _git(repo, "checkout", "-b", "relay/plan")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "feature.py").write_text("x = 1\n")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-m", "feat: work (WL-1)")  # committed, then the run stopped
    state.save(
        repo,
        state.RelayState(
            plan=str(repo / "plan.md"),
            branch="relay/plan",
            task_id="WL-1",
            round=1,
            base_commit=base,
            paused_reason="interrupted",
            log_path="",
        ),
    )
    assert cli.main(["resume", "--repo", str(repo)]) == cli.EXIT_OK, capsys.readouterr().err


def test_resume_returns_to_the_saved_branch(repo: Path, monkeypatch):
    """Resuming from main must not let the agents commit to main."""
    _git(repo, "branch", "relay/plan")
    state.save(
        repo,
        state.RelayState(
            plan=str(repo / "plan.md"),
            branch="relay/plan",
            task_id="WL-1",
            round=1,
            base_commit=_git(repo, "rev-parse", "HEAD"),
            paused_reason="paused",
            log_path="",
        ),
    )
    monkeypatch.setattr(cli.loop, "run_plan", lambda *a, **k: [])
    assert cli.main(["resume", "--repo", str(repo)]) == cli.EXIT_OK
    assert gitcheck.current_branch(repo) == "relay/plan"
