"""A plan commit that only adds or edits unchecked tasks after the current one
slides the task's base forward like a settings commit; anything else pauses."""

import subprocess
from pathlib import Path

import pytest

from whyline_relay import loop, plan
from test_loop_pipeline import (  # noqa: F401
    _commit_one_file_then,
    _task_commit,
    make_pipeline,
    repo,
    settings_with_pipeline,
)

PLAN = """# Plan

- [x] T-0: done already
- [ ] T-1: build it
  with some detail
- [ ] T-2: later work
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root: Path, message: str, files: dict[str, str]) -> str:
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        _git(root, "add", "--", name)
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def planned(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    _commit(tmp_path, "initial", {"README.md": "x\n", "plan.md": PLAN})
    return tmp_path


def _current(root: Path) -> plan.Task:
    task = plan.find(plan.parse((root / "plan.md").read_text()), "T-1")
    assert task is not None
    return task


def _advance(root: Path, base: str, *, plan_path: Path | None = None) -> str | None:
    return loop._advance_base_past_settings(
        root,
        _current(root),
        base,
        echo=True,
        plan_path=root / "plan.md" if plan_path is None else plan_path,
    )


def test_adding_a_task_after_the_current_one_advances_the_base(planned, capsys):
    base = _git(planned, "rev-parse", "HEAD")
    sha = _commit(
        planned,
        "plan: add T-3",
        {"plan.md": PLAN + "- [ ] T-3: inserted later\n  relay-profile: full\n"},
    )
    assert _advance(planned, base) == sha
    short = sha[:7]
    assert (
        f"Plan changed during T-1 ({short} plan: add T-3); continuing from {short}."
        in capsys.readouterr().out
    )


def test_editing_an_unchecked_later_task_is_accepted(planned):
    base = _git(planned, "rev-parse", "HEAD")
    sha = _commit(
        planned,
        "plan: reword T-2",
        {"plan.md": PLAN.replace("T-2: later work", "T-2: later work, reworded")},
    )
    assert _advance(planned, base) == sha


@pytest.mark.parametrize(
    "edited",
    [
        PLAN.replace("T-1: build it", "T-1: build something else"),
        PLAN.replace("  with some detail", "  with other detail"),
        PLAN.replace("T-0: done already", "T-0: done already, reworded"),
        PLAN.replace("- [x] T-0", "- [ ] T-0b: new first\n- [x] T-0"),
        PLAN.replace("- [ ] T-1", "- [ ] T-0b: inserted before\n- [ ] T-1"),
        PLAN.replace("- [ ] T-2: later work\n", ""),
    ],
    ids=[
        "current-text",
        "current-detail",
        "earlier-text",
        "added-first",
        "added-before-current",
        "removed-later",
    ],
)
def test_changing_the_current_or_an_earlier_task_is_refused(planned, edited):
    base = _git(planned, "rev-parse", "HEAD")
    _commit(planned, "plan: edit", {"plan.md": edited})
    assert _advance(planned, base) is None


@pytest.mark.parametrize(
    "edited",
    [
        PLAN.replace("- [ ] T-1", "- [x] T-1"),
        PLAN.replace("- [ ] T-2", "- [x] T-2"),
        PLAN.replace("- [x] T-0", "- [ ] T-0"),
        PLAN + "- [x] T-3: added already ticked\n",
    ],
    ids=["tick-current", "tick-later", "untick-earlier", "add-ticked"],
)
def test_ticking_or_unticking_any_task_is_refused(planned, edited):
    base = _git(planned, "rev-parse", "HEAD")
    _commit(planned, "plan: tick", {"plan.md": edited})
    assert _advance(planned, base) is None


def test_unticking_a_ticked_later_task_is_refused(planned):
    ticked = PLAN + "- [x] T-3: already done\n"
    _commit(planned, "plan: setup", {"plan.md": ticked})
    base = _git(planned, "rev-parse", "HEAD")
    _commit(planned, "plan: untick", {"plan.md": ticked.replace("- [x] T-3", "- [ ] T-3")})
    assert _advance(planned, base) is None


def test_reordering_unchecked_later_tasks_is_refused(planned):
    three = PLAN + "- [ ] T-3: even later\n"
    _commit(planned, "plan: setup", {"plan.md": three})
    base = _git(planned, "rev-parse", "HEAD")
    swapped = three.replace(
        "- [ ] T-2: later work\n- [ ] T-3: even later\n",
        "- [ ] T-3: even later\n- [ ] T-2: later work\n",
    )
    _commit(planned, "plan: swap T-2 and T-3", {"plan.md": swapped})
    assert _advance(planned, base) is None


def test_inserting_between_later_tasks_keeps_their_order_and_is_accepted(planned):
    three = PLAN + "- [ ] T-3: even later\n"
    _commit(planned, "plan: setup", {"plan.md": three})
    base = _git(planned, "rev-parse", "HEAD")
    sha = _commit(
        planned,
        "plan: add T-2b",
        {"plan.md": three.replace("- [ ] T-3", "- [ ] T-2b: in between\n- [ ] T-3")},
    )
    assert _advance(planned, base) == sha


def test_a_plan_plus_code_commit_is_refused(planned):
    base = _git(planned, "rev-parse", "HEAD")
    _commit(
        planned,
        "plan: add T-3 and code",
        {"plan.md": PLAN + "- [ ] T-3: later\n", "README.md": "changed\n"},
    )
    assert _advance(planned, base) is None


def test_a_plan_commit_with_the_planning_note_is_accepted(planned, capsys):
    base = _git(planned, "rev-parse", "HEAD")
    sha = _commit(
        planned,
        "plan: add T-3",
        {
            "plan.md": PLAN + "- [ ] T-3: later\n",
            ".whyline/decisions.md": "## add T-3\n",
        },
    )
    assert _advance(planned, base) == sha
    assert "Plan changed during T-1" in capsys.readouterr().out


def test_a_decisions_only_commit_alongside_a_plan_commit_is_accepted(planned, capsys):
    base = _git(planned, "rev-parse", "HEAD")
    first = _commit(planned, "plan: add T-3", {"plan.md": PLAN + "- [ ] T-3: later\n"})
    second = _commit(planned, "note: why T-3", {".whyline/decisions.md": "## T-3\n"})
    assert _advance(planned, base) == second
    out = capsys.readouterr().out
    assert f"Plan changed during T-1 ({first[:7]} plan: add T-3)" in out
    assert f"Plan changed during T-1 ({second[:7]} note: why T-3)" in out


def test_a_decisions_only_commit_on_its_own_is_refused(planned):
    base = _git(planned, "rev-parse", "HEAD")
    _commit(planned, "note: something", {".whyline/decisions.md": "## x\n"})
    assert _advance(planned, base) is None


def test_settings_and_plan_commits_together_are_accepted(planned, capsys):
    base = _git(planned, "rev-parse", "HEAD")
    settings = _commit(
        planned, "setup: grok", {".whyline/relay/config.toml": 'plan = "plan.md"\n'}
    )
    plan_sha = _commit(planned, "plan: add T-3", {"plan.md": PLAN + "- [ ] T-3: later\n"})
    assert _advance(planned, base) == plan_sha
    out = capsys.readouterr().out
    assert f"Settings changed during T-1 ({settings[:7]} setup: grok)" in out
    assert f"Plan changed during T-1 ({plan_sha[:7]} plan: add T-3)" in out


def test_a_plan_outside_the_task_repository_is_never_accepted(planned, tmp_path_factory):
    base = _git(planned, "rev-parse", "HEAD")
    _commit(planned, "plan: add T-3", {"plan.md": PLAN + "- [ ] T-3: later\n"})
    elsewhere = tmp_path_factory.mktemp("other") / "plan.md"
    assert _advance(planned, base, plan_path=elsewhere) is None


def test_without_a_plan_path_a_plan_commit_still_pauses(planned):
    base = _git(planned, "rev-parse", "HEAD")
    _commit(planned, "plan: add T-3", {"plan.md": PLAN + "- [ ] T-3: later\n"})
    assert (
        loop._advance_base_past_settings(planned, _current(planned), base, echo=False)
        is None
    )


def test_a_plan_commit_during_a_running_task_moves_the_base(repo, monkeypatch, capsys):
    original = "- [ ] T-1: build it\n"
    _commit(repo, "plan", {"plan.md": original})
    task = plan.find(plan.parse(original), "T-1")
    monkeypatch.setattr(
        loop.agents,
        "run",
        _commit_one_file_then(
            repo / "plan.md",
            "plan: add T-2",
            ["claude:ready", "claude:passed", "claude:approved"],
            original + "- [ ] T-2: later\n",
        ),
    )
    outcome = loop.run_task(
        repo,
        settings_with_pipeline(repo, make_pipeline()),
        task,
        base_commit=_git(repo, "rev-parse", "HEAD"),
        echo=True,
        plan_path=repo / "plan.md",
    )
    assert outcome.committed is True
    plan_sha = next(
        line.split(" ", 1)[0]
        for line in _git(repo, "log", "--pretty=format:%H %s").splitlines()
        if "plan: add T-2" in line
    )
    task_sha = _task_commit(repo, "T-1")
    assert _git(repo, "rev-parse", f"{task_sha}^") == plan_sha
    short = plan_sha[:7]
    assert (
        f"Plan changed during T-1 ({short} plan: add T-2); continuing from {short}."
        in capsys.readouterr().out
    )
