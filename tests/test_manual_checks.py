"""Manual checks block a release until they are confirmed."""

import subprocess
import sys
from pathlib import Path

from whyline_relay import cli, plan, state, testplan

FAKE = str(Path(__file__).parent / "fake_pipeline_agent.py")

PLAN = """\
- [x] OLD: shipped earlier
  Already gone.
- [x] REL-0: previous release
  relay-profile: release
- [x] T-1: build the badge
  Show the badge.
- [ ] T-2: release 1.0
  relay-profile: release
  Bump the version.
  Tag v1.0 and push.
- [ ] T-3: after
  x
"""

TESTS = """\
- [x] TP-0: Already confirmed
  covers: T-1
  kind: manual
  steps:
    - look once
  expected:
    - it was fine
- [ ] TP-1: Badge is visible
  covers: T-1
  kind: manual
  steps:
    - open the window
  expected:
    - the badge shows the count
- [ ] TP-2: Tray menu opens
  covers: T-1
  kind: manual
  steps:
    - click the tray icon
  expected:
    - the menu lists quit
- [ ] TP-3: Count is stored
  covers: T-1
  kind: auto
  steps:
    - read the count
  expected:
    - the count is one
- [ ] TP-OLD: Previous phase
  covers: OLD
  kind: manual
  steps:
    - open the old screen
  expected:
    - the old screen is there
- [ ] TP-LATER: After this release
  covers: T-3
  kind: manual
  steps:
    - open the next screen
  expected:
    - the next screen is there
"""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return result.stdout


def _last_commit(root: Path) -> tuple[str, list[str]]:
    subject = _git(root, "log", "-1", "--pretty=format:%s")
    files = _git(root, "show", "--name-only", "--pretty=format:", "HEAD").split()
    return subject, files


def _agent_config(root: Path) -> str:
    return (
        '[roles]\n'
        'implementer = "codex"\n'
        'reviewer = "claude"\n'
        'release = "grok"\n'
        '[agents.codex]\n'
        f'command = ["{sys.executable}", "{FAKE}", "claude:ready-for-review", "{root}"]\n'
        '[agents.grok]\n'
        'adapter = "generic"\n'
        f'command = ["{sys.executable}", "{FAKE}", "claude:ready-for-review", "{root}"]\n'
        '[agents.claude]\n'
        f'command = ["{sys.executable}", "{FAKE}", "commit:claude:approved", "{root}"]\n'
    )


def _ready(two_role_repo, *, agent: bool = False) -> Path:
    root = two_role_repo(PLAN)
    (root / "plan.tests.md").write_text(TESTS)
    paths = ["plan.tests.md"]
    if agent:
        (root / ".whyline/relay/config.toml").write_text(_agent_config(root))
        paths.append(".whyline/relay/config.toml")
    _git(root, "add", "--", *paths)
    _git(root, "commit", "-m", "test plan", "--", *paths)
    return root


def _release_log(root: Path) -> Path:
    return root / ".whyline" / "relay" / "logs" / "T-2-1-grok.log"


def _task_checked(root: Path, task_id: str) -> bool:
    tasks = plan.parse((root / "plan.md").read_text())
    found = plan.find(tasks, task_id)
    assert found is not None
    return found.checked


def test_two_open_manual_checks_pause_before_the_release_and_list_both(two_role_repo, capsys):
    root = _ready(two_role_repo)
    code = cli.main(["start", "--repo", str(root)])
    assert code == cli.EXIT_PAUSED
    err = capsys.readouterr().err
    assert "Manual checks before T-2:" in err
    assert (
        "TP-1: Badge is visible | steps: open the window | "
        "expected: the badge shows the count"
    ) in err
    assert (
        "TP-2: Tray menu opens | steps: click the tray icon | "
        "expected: the menu lists quit"
    ) in err
    for absent in ("TP-0", "TP-3", "TP-OLD", "TP-LATER"):
        assert absent not in err
    assert "Confirm with:" in err and "manual" in err
    assert not _task_checked(root, "T-2")
    assert not _release_log(root).exists()

    code = cli.main(["manual", "--repo", str(root)])
    assert code == 0
    listed = capsys.readouterr().out
    assert listed.splitlines()[0] == "Manual checks before T-2:"
    assert "TP-1: Badge is visible" in listed
    assert "TP-2: Tray menu opens" in listed
    assert "TP-OLD" not in listed
    assert "TP-LATER" not in listed


def test_pass_ticks_and_commits_only_the_test_plan(two_role_repo, capsys):
    root = _ready(two_role_repo)
    assert cli.main(["start", "--repo", str(root)]) == cli.EXIT_PAUSED
    capsys.readouterr()
    (root / "scratch.txt").write_text("leave me\n")

    code = cli.main(
        ["manual", "--repo", str(root), "TP-1", "pass", "looks right"]
    )
    assert code == 0, capsys.readouterr()
    scenarios = {item.id: item for item in testplan.parse((root / "plan.tests.md").read_text())}
    assert scenarios["TP-1"].checked
    assert scenarios["TP-1"].note == "looks right"
    assert not scenarios["TP-2"].checked
    subject, files = _last_commit(root)
    assert subject == "test: confirm TP-1"
    assert files == ["plan.tests.md"]
    status = _git(root, "status", "--porcelain")
    assert "scratch.txt" in status
    assert "plan.tests.md" not in status
    saved = state.load(root)
    assert saved is not None
    assert saved.paused_reason.startswith("Manual checks before T-2:")
    assert "TP-1:" not in saved.paused_reason
    assert "TP-2:" in saved.paused_reason
    assert not _task_checked(root, "T-2")


def test_fail_keeps_the_pause_and_records_the_note(two_role_repo, capsys):
    root = _ready(two_role_repo)
    assert cli.main(["start", "--repo", str(root)]) == cli.EXIT_PAUSED
    capsys.readouterr()

    code = cli.main(
        ["manual", "--repo", str(root), "TP-2", "fail", "badge", "overlaps", "the", "title"]
    )
    assert code == 0, capsys.readouterr()
    scenario = next(
        item
        for item in testplan.parse((root / "plan.tests.md").read_text())
        if item.id == "TP-2"
    )
    assert not scenario.checked
    assert scenario.note == "FAILED badge overlaps the title"
    subject, files = _last_commit(root)
    assert subject == "test: fail TP-2"
    assert files == ["plan.tests.md"]
    saved = state.load(root)
    assert saved is not None
    assert saved.paused_reason.startswith("Manual checks before T-2:")
    assert "TP-2:" in saved.paused_reason
    assert not _task_checked(root, "T-2")

    code = cli.main(["resume", "--repo", str(root)])
    assert code == cli.EXIT_ERROR
    assert "TP-2:" in capsys.readouterr().err
    assert state.load(root).paused_reason.startswith("Manual checks before T-2:")
    assert not _release_log(root).exists()


def test_done_is_refused_while_a_manual_check_is_open(two_role_repo, capsys):
    root = _ready(two_role_repo)
    assert cli.main(["start", "--repo", str(root)]) == cli.EXIT_PAUSED
    capsys.readouterr()
    assert cli.main(["manual", "--repo", str(root), "TP-1", "pass"]) == 0
    capsys.readouterr()

    code = cli.main(["done", "T-2", "--repo", str(root)])
    captured = capsys.readouterr()
    assert code == cli.EXIT_ERROR
    assert "TP-2:" in captured.err
    assert "TP-1:" not in captured.err
    assert "Marked" not in captured.out
    assert not _task_checked(root, "T-2")
    assert "release task T-2 done by hand" not in _git(root, "log", "--pretty=format:%s")

    code = cli.main(["resume", "--repo", str(root)])
    assert code == cli.EXIT_ERROR
    assert "TP-2:" in capsys.readouterr().err
    assert state.load(root).paused_reason.startswith("Manual checks before T-2:")


def test_resume_starts_the_release_once_every_manual_check_passed(two_role_repo, capsys):
    root = _ready(two_role_repo)
    assert cli.main(["start", "--repo", str(root)]) == cli.EXIT_PAUSED
    capsys.readouterr()
    assert cli.main(["manual", "--repo", str(root), "TP-1", "pass"]) == 0
    capsys.readouterr()
    code = cli.main(["manual", "--repo", str(root), "TP-2", "pass", "menu opens"])
    assert code == 0
    assert "All manual checks passed" in capsys.readouterr().out

    code = cli.main(["resume", "--repo", str(root)])
    assert code == cli.EXIT_PAUSED
    err = capsys.readouterr().err
    assert "release task for you: T-2" in err
    assert "Bump the version." in err
    assert "Manual checks" not in err
    saved = state.load(root)
    assert saved is not None
    assert saved.paused_reason.startswith("release task for you: T-2")
    assert not _task_checked(root, "T-2")


def test_release_agent_waits_for_manual_checks_then_starts_on_resume(two_role_repo, capsys):
    root = _ready(two_role_repo, agent=True)
    code = cli.main(["start", "--repo", str(root)])
    assert code == cli.EXIT_PAUSED
    assert "Manual checks before T-2:" in capsys.readouterr().err
    assert not _release_log(root).exists()

    code = cli.main(["resume", "--repo", str(root)])
    assert code == cli.EXIT_ERROR
    assert "TP-1:" in capsys.readouterr().err
    assert not _release_log(root).exists()

    assert cli.main(["manual", "--repo", str(root), "TP-1", "pass"]) == 0
    assert cli.main(["manual", "--repo", str(root), "TP-2", "pass"]) == 0
    capsys.readouterr()
    code = cli.main(["resume", "--repo", str(root)])
    assert code == cli.EXIT_OK, capsys.readouterr()
    assert _release_log(root).exists()
    assert _task_checked(root, "T-2")


def test_a_plan_without_a_test_plan_never_pauses_for_manual_checks(two_role_repo, capsys):
    root = two_role_repo(PLAN)
    assert not (root / "plan.tests.md").exists()
    code = cli.main(["start", "--repo", str(root)])
    assert code == cli.EXIT_PAUSED
    err = capsys.readouterr().err
    assert "release task for you: T-2" in err
    assert "Manual checks" not in err
    assert "Bump the version." in err
