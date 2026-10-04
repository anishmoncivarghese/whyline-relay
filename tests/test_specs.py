import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whyline_relay import gitcheck, loop, planner, specs, state
from tests.test_planner import _scripted_run, repo, settings_with_planner  # noqa: F401


def test_spec_draft_review_then_approve_commits_only_the_spec(repo, monkeypatch):
    import subprocess

    settings = settings_with_planner(repo)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["claude:ready", "claude:approved"]))
    path = specs.draft(repo, settings, "Build the thing")
    assert path == repo / ".whyline/relay/draft-spec.md"
    path.write_text("# Spec\n\n## Why\nBecause.\n")
    target = specs.approve(repo, path, name="The Thing")
    assert target == repo / "docs/specs/the-thing.md"
    shown = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=repo,
                           capture_output=True, text=True).stdout.split()
    assert shown == ["docs/specs/the-thing.md"]
    assert state.load_plan(repo, name="spec-state.json") is None


def test_spec_and_plan_checkpoints_do_not_collide(repo, monkeypatch):
    settings = settings_with_planner(repo)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["codex:blocked#Q?", "codex:blocked#P?"]))
    with pytest.raises(specs.SpecQuestions):
        specs.draft(repo, settings, "spec request")
    with pytest.raises(planner.PlanQuestions):
        planner.draft(repo, settings, "plan request")
    assert specs.pending_description(repo) == "spec request"
    assert planner.pending_description(repo) == "plan request"


def test_spec_answer_reruns_the_asking_stage(repo, monkeypatch):
    settings = settings_with_planner(repo)
    prompts = []
    scripted = _scripted_run(["codex:blocked#Which DB?", "claude:ready", "claude:approved"])

    def run(command, prompt, **kw):
        prompts.append(prompt)
        return scripted(command, prompt, **kw)

    monkeypatch.setattr(loop.agents, "run", run)
    with pytest.raises(specs.SpecQuestions):
        specs.draft(repo, settings, "x")
    specs.answer(repo, settings, "sqlite")
    assert "You asked:\n1. Which DB?\nThe human answered:\nsqlite" in prompts[1]


def test_spec_prompts_exist():
    from whyline_relay import prompts as p
    assert "## Why" in p.SPEC_DRAFT and "## Testing" in p.SPEC_DRAFT
    assert "placeholder" in p.SPEC_REVIEW


def test_spec_discard(repo, monkeypatch):
    settings = settings_with_planner(repo)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["codex:blocked#Q?"]))
    with pytest.raises(specs.SpecQuestions):
        specs.draft(repo, settings, "spec request")
    assert state.load_plan(repo, name="spec-state.json") is not None
    msg = specs.discard(repo)
    assert "Discarded" in msg
    assert state.load_plan(repo, name="spec-state.json") is None
    assert specs.discard(repo) == "Nothing to discard."


def test_spec_approve_validation(repo):
    draft = repo / ".whyline/relay/draft-spec.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("   \n")
    with pytest.raises(ValueError, match="empty"):
        specs.approve(repo, draft, name="empty-spec")

    draft.write_text("# Real Spec\n")
    specs.approve(repo, draft, name="my-spec")
    with pytest.raises(planner.PlanExists):
        specs.approve(repo, draft, name="my-spec", replace=False)
    # With replace=True it should succeed
    draft.write_text("# Real Spec Updated\n")
    replaced = specs.approve(repo, draft, name="my-spec", replace=True)
    assert replaced.read_text() == "# Real Spec Updated\n"


def test_relay_ignore_includes_spec_files():
    assert ".whyline/relay/draft-spec.md" in gitcheck.RELAY_IGNORE
    assert ".whyline/relay/spec-state.json*" in gitcheck.RELAY_IGNORE
