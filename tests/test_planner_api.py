import subprocess
from pathlib import Path
import pytest
from whyline_relay import config, plan, planner, state


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def test_validate_accepts_a_real_plan():
    assert planner.validate("- [ ] T-1: build it\n  Do the thing.\n") == []


def test_validate_accepts_crlf_and_no_trailing_newline():
    assert planner.validate("- [ ] T-1: build it\r\n  Do it.\r\n- [ ] T-2: more") == []


@pytest.mark.parametrize("text", ["", "   \n", "Just some prose about the plan.\n"])
def test_validate_rejects_text_with_no_tasks(text):
    problems = planner.validate(text)
    assert problems and "no tasks" in problems[0]


def test_validate_reports_duplicate_ids():
    problems = planner.validate("- [ ] T-1: a\n- [ ] T-1: b\n")
    assert problems and "duplicate task id" in problems[0]


def test_approve_writes_and_commits_only_plan_md(repo: Path):
    (repo / "unrelated.txt").write_text("keep me uncommitted\n")
    draft = repo / ".whyline" / "relay" / "d.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("- [ ] T-1: x\n  y.\n")
    target = planner.approve(repo, config.load(repo), draft, drafted_by="codex")
    assert target == repo / "plan.md"
    assert target.read_text() == "- [ ] T-1: x\n  y.\n"
    assert _git(repo, "log", "-1", "--format=%s").strip() == "docs: add plan drafted by codex"
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["plan.md"]
    assert "unrelated.txt" in _git(repo, "status", "--porcelain")


def test_approve_refuses_to_replace_without_permission(repo: Path):
    (repo / "plan.md").write_text("- [ ] OLD-1: old\n")
    draft = repo / "d.md"
    draft.write_text("- [ ] T-1: new\n")
    with pytest.raises(planner.PlanExists):
        planner.approve(repo, config.load(repo), draft, drafted_by="codex")
    assert (repo / "plan.md").read_text() == "- [ ] OLD-1: old\n"
    planner.approve(repo, config.load(repo), draft, drafted_by="codex", replace=True)
    assert (repo / "plan.md").read_text() == "- [ ] T-1: new\n"


def test_approve_rejects_an_invalid_draft(repo: Path):
    draft = repo / "d.md"
    draft.write_text("no tasks here\n")
    with pytest.raises(plan.PlanError, match="no tasks"):
        planner.approve(repo, config.load(repo), draft, drafted_by="codex")
    assert not (repo / "plan.md").exists()


def test_approve_can_clear_the_planner_checkpoint(repo: Path):
    state.save_plan(repo, state.PlanState(
        description="d", stage="@complete", round=1, stage_visits={}, agent="",
        feedback="", draft_path=str(planner.draft_path(repo)), paused_reason="",
        log_path="",
    ))
    draft = repo / "d.md"
    draft.write_text("- [ ] T-1: x\n")
    planner.approve(repo, config.load(repo), draft, drafted_by="codex", clear_checkpoint=True)
    assert state.load_plan(repo) is None


import sys
from whyline_relay import loop

FAKE = str(Path(__file__).parent / "fake_pipeline_agent.py")


def _settings(root: Path) -> config.Config:
    base = config.load(root)
    return config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={"codex": ["codex"], "claude": ["claude"]},
        status_map=base.status_map,
        planner=config.PlannerConfig(draft="codex", review="claude", max_visits=3),
    )


def _scripted(repo: Path, specs: list[str], prompts: list[str]):
    """Each turn pops one "to_actor:status" spec; the drafting agent (codex)
    also writes the draft file, like a real one would."""

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
    ):
        prompts.append(prompt)
        if agent_name == "codex":
            planner.draft_path(repo).write_text("- [ ] T-1: build it\n")
        result = subprocess.run(
            [sys.executable, FAKE, specs.pop(0), str(cwd), prompt],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(result.stdout + result.stderr)
        return result.returncode

    return run


@pytest.fixture
def quiet_whyline(monkeypatch):
    monkeypatch.setattr(
        loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET"
    )
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    monkeypatch.setattr(planner.whylinecmd, "claim", lambda *a, **k: None)


def test_draft_returns_the_draft_and_reports_each_stage(
    repo, monkeypatch, quiet_whyline
):
    prompts, lines = [], []
    monkeypatch.setattr(
        loop.agents, "run", _scripted(repo, ["claude:ready", "claude:approved"], prompts)
    )
    path = planner.draft(repo, _settings(repo), "a health check", print_fn=lines.append)
    assert path == planner.draft_path(repo)
    assert path.read_text() == "- [ ] T-1: build it\n"
    assert lines == ["codex is drafting the plan", "claude is reviewing the draft"]
    assert planner.pending_description(repo) == "a health check"


def test_draft_refuses_while_another_draft_is_checkpointed(
    repo, monkeypatch, quiet_whyline
):
    monkeypatch.setattr(
        loop.agents, "run", _scripted(repo, ["claude:ready", "claude:approved"], [])
    )
    planner.draft(repo, _settings(repo), "first")
    with pytest.raises(planner.PlanAlreadyInProgress):
        planner.draft(repo, _settings(repo), "second")


def test_revise_sends_the_feedback_to_the_drafting_agent(
    repo, monkeypatch, quiet_whyline
):
    prompts = []
    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted(
            repo,
            ["claude:ready", "claude:approved", "claude:ready", "claude:approved"],
            prompts,
        ),
    )
    planner.draft(repo, _settings(repo), "a health check")
    planner.revise(repo, _settings(repo), "split T-1 into two tasks")
    assert "split T-1 into two tasks" in prompts[2]


def test_revise_without_a_draft_says_so(repo):
    with pytest.raises(planner.NoPlanInProgress):
        planner.revise(repo, _settings(repo), "anything")


def test_resume_draft_at_complete_runs_no_agent(repo, monkeypatch, quiet_whyline):
    monkeypatch.setattr(
        loop.agents, "run", _scripted(repo, ["claude:ready", "claude:approved"], [])
    )
    planner.draft(repo, _settings(repo), "a health check")
    monkeypatch.setattr(loop.agents, "run", lambda *a, **k: pytest.fail("ran an agent"))
    assert planner.resume_draft(repo, _settings(repo)) == planner.draft_path(repo)


def test_pending_description_is_none_without_a_draft(repo):
    assert planner.pending_description(repo) is None
