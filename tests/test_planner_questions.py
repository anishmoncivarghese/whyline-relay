import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whyline_relay import brainstorm, loop, planner, prompts, state
from tests.test_planner import DESCRIPTION, _scripted_run, repo, settings_with_planner  # noqa: F401


def test_a_blocked_handoff_with_questions_raises_plan_questions(repo, monkeypatch):
    settings = settings_with_planner(repo)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(
        ["codex:blocked#Which broker? (a) Kite (b) Upstox|Paper trading in V1?"]
    ))
    with pytest.raises(planner.PlanQuestions) as raised:
        planner.draft(repo, settings, DESCRIPTION)
    assert raised.value.questions == ("Which broker? (a) Kite (b) Upstox", "Paper trading in V1?")
    assert raised.value.stage == "draft" and raised.value.agent == "codex"
    assert state.load_plan(repo).stage == "draft"


def test_blocked_without_questions_is_still_a_plain_pause(repo, monkeypatch):
    settings = settings_with_planner(repo)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["codex:blocked"]))
    with pytest.raises(loop.Paused) as raised:
        planner.draft(repo, settings, DESCRIPTION)
    assert not isinstance(raised.value, planner.PlanQuestions)


def test_answer_reruns_the_stage_that_asked_with_the_answers(repo, monkeypatch):
    settings = settings_with_planner(repo)
    prompts_seen = []
    scripted = _scripted_run(["claude:ready", "claude:blocked#Which broker?", "claude:approved"])

    def run(command, prompt, **kwargs):
        prompts_seen.append(prompt)
        return scripted(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", run)
    with pytest.raises(planner.PlanQuestions):
        planner.draft(repo, settings, DESCRIPTION)
    assert state.load_plan(repo).stage == "review"
    path = planner.answer(repo, settings, "Kite")
    assert path == planner.draft_path(repo)
    assert len(prompts_seen) == 3
    assert "You asked:\n1. Which broker?\nThe human answered:\nKite" in prompts_seen[2]
    assert state.load_plan(repo).stage == "@complete"


def test_resume_shows_the_questions_again(repo, monkeypatch):
    settings = settings_with_planner(repo)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["codex:blocked#Which broker?"]))
    with pytest.raises(planner.PlanQuestions):
        planner.draft(repo, settings, DESCRIPTION)
    with pytest.raises(planner.PlanQuestions) as raised:
        planner.resume_draft(repo, settings)
    assert raised.value.questions == ("Which broker?",)


def test_answer_feedback_without_recorded_questions():
    assert planner.answer_feedback((), "use Kite") == (
        "The human answered your questions:\nuse Kite"
    )


def test_prompts_ask_for_choices_inside_questions():
    for text in (prompts.PLAN_DRAFT, prompts.PLAN_REVIEW, brainstorm.PLAN_GENERATION_PROMPT):
        assert "(a)" in text and "(b)" in text
    assert "## Open questions" in brainstorm.PLAN_GENERATION_PROMPT
