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
