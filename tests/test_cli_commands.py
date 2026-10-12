import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from whyline_relay import cli, gitcheck, preflight, state

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


def _write_running(repo: Path, *, pid: int, agent: str = "codex") -> Path:
    target = repo / ".whyline" / "relay" / "running.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "agent": agent,
                "task": "WL-1",
                "round": 2,
                "started": (
                    datetime.now().astimezone() - timedelta(seconds=65)
                ).isoformat(),
                "pid": pid,
            }
        )
    )
    return target


def test_status_reports_a_live_turn_before_paused_details(repo: Path, capsys):
    _write_running(repo, pid=os.getpid(), agent="claude")
    state.save(
        repo,
        state.RelayState(
            plan="plan.md",
            branch="relay/plan",
            task_id="WL-1",
            round=2,
            base_commit="abc",
            paused_reason="prior pause",
            log_path="",
        ),
    )

    assert cli.main(["status", "--repo", str(repo)]) == cli.EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Running: claude reviewing WL-1, round 2, since ")
    assert " ago)" in lines[0]
    assert "prior pause" in "\n".join(lines[1:])


def test_status_ignores_a_stale_marker(repo: Path, capsys):
    target = _write_running(repo, pid=2**31 - 1)

    assert cli.main(["status", "--repo", str(repo)]) == cli.EXIT_OK
    assert capsys.readouterr().out == "No relay run in progress.\n"
    assert target.exists()


@pytest.mark.parametrize("command", ["start", "resume"])
def test_start_and_resume_refuse_while_a_relay_is_live(
    repo: Path, capsys, command: str
):
    _write_running(repo, pid=os.getpid())

    assert cli.main([command, "--repo", str(repo)]) == cli.EXIT_ERROR
    error = capsys.readouterr().err.lower()
    assert str(os.getpid()) in error
    assert "another relay is running here" in error


def test_stale_marker_does_not_block_start(repo: Path, monkeypatch, capsys):
    _write_running(repo, pid=2**31 - 1)
    monkeypatch.setattr(cli.loop, "run_plan", lambda *args, **kwargs: [])

    code = cli.main(["start", "--repo", str(repo), "--allow-dirty"])

    assert code == cli.EXIT_OK, capsys.readouterr().err


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


def _tree_check(root, plan_path, *, allow_dirty):
    """Stand in for preflight's clean-tree check: a dirty tree fails the launch."""
    if allow_dirty or not gitcheck.is_dirty(root):
        return [preflight.Check("ok", "working tree is clean")]
    return [
        preflight.Check(
            "FAIL",
            "working tree has uncommitted changes",
            "commit or stash your changes, or pass --allow-dirty",
        )
    ]


def _paused_with_only_the_decision_log_dirty(repo: Path, task_id: str = "BAK-3") -> None:
    log = repo / ".whyline" / "decisions.md"
    log.write_text("# Decisions\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "track the decision log")
    log.write_text("# Decisions\n\n- note\n")
    state.save(
        repo,
        state.RelayState(
            plan=str(repo / "plan.md"),
            branch="relay/plan",
            task_id=task_id,
            round=3,
            base_commit=_git(repo, "rev-parse", "HEAD"),
            paused_reason="paused",
            log_path="",
        ),
    )
    gitcheck.ensure_relay_ignored(repo)


def test_resume_commits_a_lone_decision_log_before_the_clean_tree_check(
    repo: Path, monkeypatch, capsys
):
    _paused_with_only_the_decision_log_dirty(repo)
    assert gitcheck.dirty_paths(repo) == [".whyline/decisions.md"]
    monkeypatch.setattr(cli, "_launch_checks", _tree_check)
    monkeypatch.setattr(cli.loop, "run_plan", lambda *args, **kwargs: [])

    code = cli.main(["resume", "--repo", str(repo)])

    captured = capsys.readouterr()
    assert code == cli.EXIT_OK, captured.err
    assert "Committed BAK-3's decision log." in captured.err
    assert gitcheck.dirty_paths(repo) == []
    assert (
        gitcheck.commit_message(repo, gitcheck.head_commit(repo))
        == "chore: record BAK-3 decisions"
    )


def test_resume_allow_dirty_leaves_the_decision_log_uncommitted(
    repo: Path, monkeypatch, capsys
):
    _paused_with_only_the_decision_log_dirty(repo)
    before = gitcheck.head_commit(repo)
    monkeypatch.setattr(cli, "_launch_checks", _tree_check)
    monkeypatch.setattr(cli.loop, "run_plan", lambda *args, **kwargs: [])

    def fail_if_called(*args, **kwargs):
        raise AssertionError("commit_decision_log should be skipped with --allow-dirty")

    monkeypatch.setattr(cli.gitcheck, "commit_decision_log", fail_if_called)

    code = cli.main(["resume", "--repo", str(repo), "--allow-dirty"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_OK, captured.err
    assert "Committed" not in captured.err
    assert gitcheck.head_commit(repo) == before
    assert gitcheck.dirty_paths(repo) == [".whyline/decisions.md"]


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


_WATCHDOG = "Watchdog disabled: a hung agent runs until you stop it."


def _write_relay_config(repo: Path, text: str) -> Path:
    path = repo / ".whyline" / "relay" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _stub_run_plan(monkeypatch, capsys) -> dict:
    seen: dict = {}

    def fake_run(root, settings, *args, **kwargs):
        seen["timeout"] = settings.timeout_minutes
        seen["err_before"] = capsys.readouterr().err
        return []

    monkeypatch.setattr(cli.loop, "run_plan", fake_run)
    return seen


def _paused(repo: Path) -> None:
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


@pytest.mark.parametrize("flag", ["none", "NONE", "unlimited", "Unlimited"])
def test_timeout_word_sets_zero_without_changing_the_toml(
    repo: Path, monkeypatch, capsys, flag: str
):
    path = _write_relay_config(repo, "timeout_minutes = 45\n")
    seen = _stub_run_plan(monkeypatch, capsys)

    code = cli.main(
        [
            "start",
            "--repo",
            str(repo),
            "--timeout",
            flag,
            "--allow-dirty",
            "--skip-checks",
        ]
    )

    assert code == cli.EXIT_OK
    assert seen["timeout"] == 0
    assert seen["err_before"] == f"{_WATCHDOG}\n"
    assert path.read_text() == "timeout_minutes = 45\n"
    assert capsys.readouterr().err == ""


def test_timeout_zero_is_kept_and_not_written(repo: Path, monkeypatch, capsys):
    path = _write_relay_config(repo, "timeout_minutes = 45\n")
    seen = _stub_run_plan(monkeypatch, capsys)

    code = cli.main(
        [
            "start",
            "--repo",
            str(repo),
            "--timeout",
            "0",
            "--allow-dirty",
            "--skip-checks",
        ]
    )

    assert code == cli.EXIT_OK
    assert seen["timeout"] == 0
    assert seen["err_before"] == f"{_WATCHDOG}\n"
    assert path.read_text() == "timeout_minutes = 45\n"


def test_a_positive_timeout_override_is_kept_and_does_not_warn(
    repo: Path, monkeypatch, capsys
):
    path = _write_relay_config(repo, "timeout_minutes = 45\n")
    seen = _stub_run_plan(monkeypatch, capsys)

    code = cli.main(
        [
            "start",
            "--repo",
            str(repo),
            "--timeout",
            "12",
            "--allow-dirty",
            "--skip-checks",
        ]
    )

    assert code == cli.EXIT_OK
    assert seen["timeout"] == 12
    assert seen["err_before"] == ""
    assert path.read_text() == "timeout_minutes = 45\n"


def test_start_warns_when_the_configured_timeout_is_zero(
    repo: Path, monkeypatch, capsys
):
    path = _write_relay_config(repo, "timeout_minutes = 0\n")
    seen = _stub_run_plan(monkeypatch, capsys)

    code = cli.main(
        ["start", "--repo", str(repo), "--allow-dirty", "--skip-checks"]
    )

    assert code == cli.EXIT_OK
    assert seen["timeout"] == 0
    assert seen["err_before"] == f"{_WATCHDOG}\n"
    assert path.read_text() == "timeout_minutes = 0\n"


def test_timeout_negative_is_an_argparse_error_and_leaves_the_file(
    repo: Path, monkeypatch, capsys
):
    path = _write_relay_config(repo, "timeout_minutes = 45\n")

    def fail(*args, **kwargs):
        raise AssertionError("the process must not start")

    monkeypatch.setattr(cli.loop, "run_plan", fail)
    with pytest.raises(SystemExit) as caught:
        cli.main(
            [
                "start",
                "--repo",
                str(repo),
                "--timeout",
                "-1",
                "--allow-dirty",
                "--skip-checks",
            ]
        )

    assert caught.value.code == 2
    assert path.read_text() == "timeout_minutes = 45\n"
    assert "Watchdog disabled" not in capsys.readouterr().err


def test_resume_warns_before_the_first_task_when_timeout_is_zero(
    repo: Path, monkeypatch, capsys
):
    _write_relay_config(repo, "timeout_minutes = 0\n")
    _paused(repo)
    seen = _stub_run_plan(monkeypatch, capsys)

    code = cli.main(
        ["resume", "--repo", str(repo), "--allow-dirty", "--skip-checks"]
    )

    assert code == cli.EXIT_OK, capsys.readouterr().err
    assert seen["timeout"] == 0
    assert seen["err_before"] == f"{_WATCHDOG}\n"


def test_resume_of_a_plan_warns_before_the_planner_when_timeout_is_zero(
    repo: Path, monkeypatch, capsys
):
    _write_relay_config(repo, "timeout_minutes = 0\n")
    state.save_plan(
        repo,
        state.PlanState(
            description="make a thing",
            stage="draft",
            round=1,
            stage_visits={},
            agent="codex",
            feedback="",
            draft_path="plan.md",
            paused_reason="paused",
            log_path="",
        ),
    )
    seen: dict = {}

    def fake_resume(root, settings, saved, **kwargs):
        seen["timeout"] = settings.timeout_minutes
        seen["err_before"] = capsys.readouterr().err
        return "resumed"

    monkeypatch.setattr(cli.planner, "resume", fake_resume)

    code = cli.main(["resume", "--repo", str(repo), "--allow-dirty", "--skip-checks"])

    assert code == cli.EXIT_OK
    assert seen["timeout"] == 0
    assert seen["err_before"] == f"{_WATCHDOG}\n"


def test_chat_does_not_print_the_watchdog_line(repo: Path, monkeypatch, capsys):
    _write_relay_config(repo, "timeout_minutes = 0\n")
    monkeypatch.setattr(cli.chat, "repl", lambda root: None)

    assert cli.main(["chat", "--repo", str(repo)]) == cli.EXIT_OK
    assert "Watchdog disabled" not in capsys.readouterr().err
