"""A task can name the repository its code lives in (`relay-repo:`), so one
relay -- its plan, state, handoffs and logs -- can drive work in another
checkout without the user switching repositories mid-plan."""

import subprocess
import sys
from pathlib import Path

import pytest

from whyline_relay import loop, plan, prompts, state
from test_loop_pipeline import FAKE, make_pipeline, settings_with_pipeline


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "T")
    (root / "README.md").write_text("x\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return root


@pytest.fixture
def repos(tmp_path: Path, monkeypatch):
    root = init_repo(tmp_path / "relay")
    work = init_repo(tmp_path / "work")
    (root / ".whyline").mkdir()
    (root / ".whyline" / "decisions.md").write_text("# Decisions\n")
    (root / ".whyline" / ".gitignore").write_text("active-handoff.json\n")  # as whyline init writes
    (root / "plan.md").write_text(
        f"- [ ] T-1: build it in the other repo\n  relay-repo: {work}\n  Do it.\n"
    )
    git(root, "add", "-A")
    git(root, "commit", "-m", "plan")
    git(root, "switch", "-c", "relay/plan")
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    return root, work


def scripted(specs: list[str], work: Path, *, edit=True, commit_in_work=False):
    """Like test_loop_pipeline._scripted_run, but the first turn edits the
    *work* repository and every turn appends a decision to the relay repo's
    log, the way `whyline note` does when the footer pins it there."""
    calls = {"n": 0}

    def run(command, prompt, *, cwd, log_path, timeout_seconds, which=None,
            echo=True, agent_name=None, cancel_event=None):
        calls["n"] += 1
        if calls["n"] == 1 and edit:
            (work / "feature.txt").write_text("done\n")
            if commit_in_work:
                git(work, "add", "-A")
                git(work, "commit", "-m", "sneaky (T-1)")
        with (Path(cwd) / ".whyline" / "decisions.md").open("a") as log:
            log.write(f"- turn {calls['n']}\n")
        spec = specs.pop(0)
        result = subprocess.run(
            [sys.executable, FAKE, spec, str(cwd), prompt],
            cwd=cwd, capture_output=True, text=True,
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(result.stdout + result.stderr)
        return result.returncode

    run.calls = calls
    return run


def test_plan_reads_the_task_repository_line(tmp_path):
    tasks = plan.parse(
        "- [ ] A-1: here\n  Do it.\n- [ ] A-2: there\n  relay-repo: /x/other\n"
    )
    assert tasks[0].repo is None
    assert tasks[1].repo == "/x/other"


def test_task_repo_defaults_to_the_relay_repo_and_resolves_relative_paths(repos):
    root, work = repos
    here = plan.Task("A-1", "A-1: x", False, 0)
    assert loop.task_repo(root, here) == root.resolve()
    relative = plan.Task("A-2", "A-2: x", False, 0, repo="../work")
    assert loop.task_repo(root, relative) == work.resolve()


def test_task_repo_must_be_the_top_of_a_git_repository(repos, tmp_path):
    root, work = repos
    (work / "sub").mkdir()
    for bad in (tmp_path / "missing", work / "sub"):
        task = plan.Task("A-1", "A-1: x", False, 0, repo=str(bad))
        with pytest.raises(loop.Paused, match="relay-repo"):
            loop.task_repo(root, task)


def test_a_cross_repo_task_commits_in_its_repo_and_ticks_in_the_relay_repo(
    repos, monkeypatch
):
    root, work = repos
    settings = settings_with_pipeline(root, make_pipeline())
    monkeypatch.setattr(
        loop.agents, "run",
        scripted(["claude:ready", "claude:passed", "claude:approved"], work),
    )
    work_main = git(work, "rev-parse", "main")

    outcomes = loop.run_plan(root, settings, root / "plan.md", branch="relay/plan", echo=False)

    assert [o.task_id for o in outcomes] == ["T-1"]
    # The code commit is in the work repo, on the relay's branch, not main.
    assert git(work, "branch", "--show-current") == "relay/plan"
    assert git(work, "rev-parse", "main") == work_main
    assert "(T-1)" in git(work, "log", "-1", "--format=%s")
    assert git(work, "show", "--name-only", "--format=", "HEAD") == "feature.txt"
    # The relay repo gets the tick and the decision log, nothing else.
    assert git(root, "log", "-1", "--format=%s") == "chore: tick T-1 in the plan"
    assert sorted(git(root, "show", "--name-only", "--format=", "HEAD").split()) == [
        ".whyline/decisions.md", "plan.md",
    ]
    assert "- [x] T-1" in (root / "plan.md").read_text()
    assert git(root, "status", "--porcelain") == ""
    assert git(work, "status", "--porcelain") == ""


def test_a_dirty_task_repo_pauses_before_any_agent_runs(repos, monkeypatch):
    root, work = repos
    (work / "stray.txt").write_text("mine\n")
    settings = settings_with_pipeline(root, make_pipeline())
    run = scripted(["claude:ready"], work)
    monkeypatch.setattr(loop.agents, "run", run)

    with pytest.raises(loop.Paused, match=r"stray\.txt"):
        loop.run_plan(root, settings, root / "plan.md", branch="relay/plan", echo=False)

    assert run.calls["n"] == 0
    assert git(work, "branch", "--show-current") == "main"
    saved = state.load(root)
    assert saved is not None and saved.task_id == "T-1"


def test_an_agent_committing_in_the_task_repo_is_refused(repos, monkeypatch):
    root, work = repos
    settings = settings_with_pipeline(root, make_pipeline())
    monkeypatch.setattr(
        loop.agents, "run",
        scripted(["claude:ready", "claude:passed", "claude:approved"], work,
                 commit_in_work=True),
    )
    with pytest.raises(loop.Paused, match="commit"):
        loop.run_plan(root, settings, root / "plan.md", branch="relay/plan", echo=False)
    assert "- [ ] T-1" in (root / "plan.md").read_text()


def test_resume_keeps_the_task_repo_base_commit(repos, monkeypatch):
    root, work = repos
    settings = settings_with_pipeline(root, make_pipeline())
    # Pause after the draft by handing off to an unknown status.
    monkeypatch.setattr(loop.agents, "run", scripted(["claude:weird"], work))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, settings, root / "plan.md", branch="relay/plan", echo=False)
    saved = state.load(root)
    assert saved.base_commit == git(work, "rev-parse", "HEAD")
    assert saved.base_commit != git(root, "rev-parse", "HEAD")


def test_the_footer_names_the_task_repo_only_when_it_differs(tmp_path):
    relay = tmp_path / "relay"
    other = tmp_path / "other"
    same = prompts.repository_footer(relay)
    assert "Task repository" not in same
    assert prompts.repository_footer(relay, relay) == same
    footer = prompts.repository_footer(relay, other)
    assert f"Task repository (generated for this turn):\n{other.resolve()}" in footer
    assert str(relay.resolve() / ".whyline" / "active-handoff.json") in footer


def test_resume_after_a_dirty_repo_pause_starts_the_task_fresh(repos, monkeypatch):
    root, work = repos
    (work / "stray.txt").write_text("mine\n")
    settings = settings_with_pipeline(root, make_pipeline())
    monkeypatch.setattr(loop.agents, "run", scripted([], work))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, settings, root / "plan.md", branch="relay/plan", echo=False)

    (work / "stray.txt").unlink()
    monkeypatch.setattr(
        loop.agents, "run",
        scripted(["claude:ready", "claude:passed", "claude:approved"], work),
    )
    loop.run_plan(
        root, settings, root / "plan.md", branch="relay/plan", resume=True, echo=False
    )
    assert "(T-1)" in git(work, "log", "-1", "--format=%s")
    assert "- [x] T-1" in (root / "plan.md").read_text()
