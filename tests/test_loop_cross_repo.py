"""A task can name the repository its code lives in (`relay-repo:`), so one
relay -- its plan, state, handoffs and logs -- can drive work in another
checkout without the user switching repositories mid-plan."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from whyline_relay import config, loop, plan, prompts, state
from test_loop_pipeline import FAKE, _scripted_run, make_pipeline, settings_with_pipeline


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
            echo=True, agent_name=None, cancel_event=None, env=None):
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


def _subjects(root: Path, base: str) -> list[str]:
    return git(root, "log", f"{base}..HEAD", "--format=%s").splitlines()


def test_a_cross_repo_approval_with_no_changes_makes_one_empty_commit_and_ticks(
    repos, monkeypatch
):
    root, work = repos
    base = git(work, "rev-parse", "HEAD")
    settings = settings_with_pipeline(root, make_pipeline())
    monkeypatch.setattr(
        loop.agents, "run",
        scripted(
            ["claude:ready", "claude:passed", "claude:approved"], work, edit=False
        ),
    )

    outcomes = loop.run_plan(
        root, settings, root / "plan.md", branch="relay/plan", echo=False
    )

    assert [o.task_id for o in outcomes] == ["T-1"]
    assert "- [x] T-1" in (root / "plan.md").read_text()
    assert _subjects(work, base) == ["chore: T-1 needed no changes (T-1)"]
    assert git(work, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") == ""
    assert git(root, "log", "-1", "--format=%s") == "chore: tick T-1 in the plan"


def test_a_same_repo_approval_with_no_changes_makes_one_empty_commit_and_ticks(
    repos, monkeypatch
):
    root, _work = repos
    (root / "plan.md").write_text("- [ ] T-1: already done\n  Nothing to change.\n")
    git(root, "add", "plan.md")
    git(root, "commit", "-m", "plan stays here")
    base = git(root, "rev-parse", "HEAD")
    settings = settings_with_pipeline(root, make_pipeline())
    monkeypatch.setattr(
        loop.agents, "run",
        _scripted_run(["claude:ready", "claude:passed", "claude:approved"]),
    )

    outcomes = loop.run_plan(
        root, settings, root / "plan.md", branch="relay/plan", echo=False
    )

    assert [o.task_id for o in outcomes] == ["T-1"]
    assert "- [x] T-1" in (root / "plan.md").read_text()
    assert _subjects(root, base) == [
        "chore: tick T-1 in the plan",
        "chore: T-1 needed no changes (T-1)",
    ]
    assert git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD~1") == ""
    assert git(root, "show", "--name-only", "--format=", "HEAD") == "plan.md"


def test_an_approval_with_changes_still_makes_one_normal_commit(repos, monkeypatch):
    root, work = repos
    base = git(work, "rev-parse", "HEAD")
    settings = settings_with_pipeline(root, make_pipeline())
    monkeypatch.setattr(
        loop.agents, "run",
        scripted(["claude:ready", "claude:passed", "claude:approved"], work),
    )

    outcomes = loop.run_plan(
        root, settings, root / "plan.md", branch="relay/plan", echo=False
    )

    assert [o.task_id for o in outcomes] == ["T-1"]
    assert "- [x] T-1" in (root / "plan.md").read_text()
    assert _subjects(work, base) == ["feat: fake claude:approved (T-1)"]
    assert git(work, "show", "--name-only", "--format=", "HEAD") == "feature.txt"
    assert git(root, "log", "-1", "--format=%s") == "chore: tick T-1 in the plan"


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


def _capture_commands(monkeypatch) -> list[tuple[list[str], str]]:
    seen: list[tuple[list[str], str]] = []

    def run(command, prompt, **kwargs):
        seen.append((list(command), prompt))
        return 0

    monkeypatch.setattr(loop.agents, "run", run)
    return seen


def _turn(root, settings, agent, task):
    loop.run_agent(
        root,
        settings,
        agent,
        "implementer",
        "implement",
        task,
        1,
        "",
        False,
        implementer="codex",
        reviewer="claude",
    )


def test_codex_and_claude_turns_carry_the_task_repo_once(repos, monkeypatch):
    root, work = repos
    seen = _capture_commands(monkeypatch)
    checked: list[tuple[str, list[str]]] = []
    real_find = loop.bypass.find

    def find(adapter_name, command):
        checked.append((adapter_name, list(command)))
        return real_find(adapter_name, command)

    monkeypatch.setattr(loop.bypass, "find", find)
    settings = config.load(root)
    task = plan.Task("T-1", "T-1: elsewhere", False, 0, repo=str(work))
    path = str(work.resolve())

    for agent in ("codex", "claude"):
        _turn(root, settings, agent, task)

    assert [agent for agent, _ in checked] == ["codex", "claude"]
    for (agent, command), (ran, prompt) in zip(checked, seen, strict=True):
        assert ran == command
        assert command == [*settings.agents[agent], "--add-dir", path]
        assert command.count("--add-dir") == 1
        assert real_find(agent, command) == []
        argv = loop.agents.build_argv(command, prompt)
        assert argv[-3:-1] == ["--add-dir", path]
        assert argv[-1] == prompt


def test_a_configured_directory_flag_is_not_repeated(repos, monkeypatch):
    root, work = repos
    path = str(work.resolve())
    relay = root / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    command = ["codex", "exec", "--add-dir", path, "-s", "workspace-write"]
    (relay / "config.toml").write_text(
        f"[agents.codex]\ncommand = {json.dumps(command)}\n",
        encoding="utf-8",
    )
    seen = _capture_commands(monkeypatch)
    task = plan.Task("T-1", "T-1: elsewhere", False, 0, repo=str(work))

    _turn(root, config.load(root), "codex", task)

    assert seen[0][0] == command
    assert seen[0][0].count("--add-dir") == 1
    assert seen[0][0].count(path) == 1


def test_a_different_directory_does_not_count_as_the_task_repo(repos, monkeypatch):
    root, work = repos
    path = str(work.resolve())
    relay = root / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    command = ["codex", "exec", "--add-dir", ".", "-s", "workspace-write"]
    (relay / "config.toml").write_text(
        f"[agents.codex]\ncommand = {json.dumps(command)}\n",
        encoding="utf-8",
    )
    seen = _capture_commands(monkeypatch)
    task = plan.Task("T-1", "T-1: elsewhere", False, 0, repo=str(work))

    _turn(root, config.load(root), "codex", task)

    assert seen[0][0] == [*command, "--add-dir", path]


def test_a_same_repo_task_and_a_planner_task_add_nothing(repos, monkeypatch):
    root, work = repos
    seen = _capture_commands(monkeypatch)
    settings = config.load(root)
    here = plan.Task("T-1", "T-1: here", False, 0)
    named_here = plan.Task("T-2", "T-2: here", False, 0, repo=str(root))
    # The planner builds a task with no relay-repo, so its turns take this path.
    planning = plan.Task("__plan__", "describe the work", False, 0)

    for task in (here, named_here, planning):
        _turn(root, settings, "codex", task)

    assert all("--add-dir" not in command for command, _ in seen)
    assert len(seen) == 3
    assert work.resolve() != root.resolve()


def test_a_generic_agent_adds_a_directory_only_when_it_opts_in(repos, monkeypatch):
    root, work = repos
    relay = root / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    bare = '[agents.aider]\nadapter = "generic"\ncommand = ["aider", "--yes"]\n'
    opted = bare + 'dir_flag = "--add-dir"\n'
    task = plan.Task("T-1", "T-1: elsewhere", False, 0, repo=str(work))
    path = str(work.resolve())

    (relay / "config.toml").write_text(bare, encoding="utf-8")
    seen = _capture_commands(monkeypatch)
    _turn(root, config.load(root), "aider", task)
    assert seen[0][0] == ["aider", "--yes"]

    (relay / "config.toml").write_text(opted, encoding="utf-8")
    _turn(root, config.load(root), "aider", task)
    assert seen[1][0] == ["aider", "--yes", "--add-dir", path]


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


def test_a_cross_repo_verify_task_commits_the_test_plan_with_the_tick(
    repos, monkeypatch
):
    root, work = repos
    (root / "plan.md").write_text(
        "- [x] MID-1: in range\n"
        "  Do the work.\n"
        "- [ ] VER-1: verify the phase\n"
        "  relay-profile: verify\n"
        f"  relay-repo: {work}\n"
    )
    (root / "plan.tests.md").write_text(
        "- [ ] TP-1: the work holds\n"
        "  covers: MID-1\n"
        "  kind: auto\n"
        "  steps:\n"
        "    - run it\n"
        "  expected:\n"
        "    - it holds\n"
        "  note: keep\n"
    )
    git(root, "add", "-A")
    git(root, "commit", "-m", "verify plan")
    captured: list[str] = []
    base = scripted(["claude:ready", "claude:passed", "claude:approved"], work)

    def run(
        command,
        prompt,
        *,
        cwd,
        log_path,
        timeout_seconds,
        which=None,
        echo=True,
        agent_name=None,
        cancel_event=None,
        env=None,
    ):
        captured.append(prompt)
        path = Path(cwd) / "plan.tests.md"
        text = path.read_text()
        if "note: keep" in text:
            path.write_text(text.replace("note: keep", "note: needs a device"))
        return base(
            command,
            prompt,
            cwd=cwd,
            log_path=log_path,
            timeout_seconds=timeout_seconds,
            which=which,
            echo=echo,
            agent_name=agent_name,
            cancel_event=cancel_event,
            env=env,
        )

    monkeypatch.setattr(loop.agents, "run", run)
    outcomes = loop.run_plan(
        root,
        settings_with_pipeline(root, make_pipeline()),
        root / "plan.md",
        branch="relay/plan",
        only="VER-1",
        echo=False,
    )
    assert [outcome.task_id for outcome in outcomes] == ["VER-1"]
    assert captured and "### TP-1:" in captured[0]
    assert "TP-9" not in captured[0]
    assert git(root, "log", "-1", "--format=%s") == "chore: tick VER-1 in the plan"
    names = git(root, "show", "--name-only", "--format=", "HEAD").split()
    assert "plan.tests.md" in names
    assert "plan.md" in names
    assert "needs a device" in git(root, "show", "HEAD:plan.tests.md")
    assert "plan.tests.md" not in git(work, "show", "--name-only", "--format=", "HEAD").split()
    assert git(root, "status", "--porcelain") == ""
    assert git(work, "status", "--porcelain") == ""
