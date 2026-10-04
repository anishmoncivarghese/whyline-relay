import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whyline_relay import loop, planner, state
from tests.test_planner import DESCRIPTION, _scripted_run, repo, settings_with_planner  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def _shot(root: Path) -> Path:
    path = root / ".whyline" / "attachments" / "s" / "1" / "shot.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    return path


def _capture(monkeypatch, specs):
    prompts = []
    commands = []
    scripted = _scripted_run(specs)

    def run(command, prompt, **kwargs):
        commands.append(list(command))
        prompts.append(prompt)
        return scripted(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", run)
    return prompts, commands


def test_draft_gives_both_stages_the_attachments_and_saves_them(repo, monkeypatch):
    shot = _shot(repo)
    prompts, commands = _capture(monkeypatch, ["claude:ready", "claude:approved"])
    planner.draft(repo, settings_with_planner(repo), DESCRIPTION, attachments=[shot])
    assert all(".whyline/attachments/s/1/shot.png" in p for p in prompts)
    assert f"--image={shot}" in commands[0]
    assert state.load_plan(repo).attachments == [".whyline/attachments/s/1/shot.png"]


def test_answer_still_carries_the_attachments(repo, monkeypatch):
    shot = _shot(repo)
    prompts, _ = _capture(monkeypatch, ["codex:blocked#Which broker?", "claude:ready", "claude:approved"])
    with pytest.raises(planner.PlanQuestions):
        planner.draft(repo, settings_with_planner(repo), DESCRIPTION, attachments=[shot])
    planner.answer(repo, settings_with_planner(repo), "Kite")
    assert ".whyline/attachments/s/1/shot.png" in prompts[1]


def test_revise_still_carries_the_attachments(repo, monkeypatch):
    shot = _shot(repo)
    prompts, _ = _capture(
        monkeypatch,
        ["claude:ready", "claude:approved", "claude:ready", "claude:approved"],
    )
    planner.draft(repo, settings_with_planner(repo), DESCRIPTION, attachments=[shot])
    planner.revise(repo, settings_with_planner(repo), "make it simpler")
    assert ".whyline/attachments/s/1/shot.png" in prompts[2]
    assert state.load_plan(repo).attachments == [".whyline/attachments/s/1/shot.png"]


def test_resume_draft_still_carries_the_attachments(repo, monkeypatch):
    shot = _shot(repo)
    prompts, _ = _capture(
        monkeypatch,
        ["claude:approved"],
    )
    # Draft finishes turn 1 (ready) and pauses before review
    settings = settings_with_planner(repo)
    state.save_plan(
        repo,
        state.PlanState(
            description=DESCRIPTION,
            stage="review",
            round=2,
            stage_visits={"draft": 1, "review": 1},
            agent="claude",
            feedback="ready",
            draft_path=str(planner.draft_path(repo)),
            paused_reason="",
            log_path="",
            attachments=[".whyline/attachments/s/1/shot.png"],
        ),
    )
    # Note: resume_draft when stage != "@complete" runs the pipeline with attachments
    planner.resume_draft(repo, settings)
    assert ".whyline/attachments/s/1/shot.png" in prompts[0]


def test_old_checkpoints_without_attachments_still_load(repo):
    (repo / ".whyline" / "relay").mkdir(parents=True, exist_ok=True)
    state.plan_path(repo).write_text(
        '{"description": "d", "stage": "draft", "round": 1, "stage_visits": {}, '
        '"agent": "", "feedback": "", "draft_path": "x", "paused_reason": "", "log_path": ""}'
    )
    assert state.load_plan(repo).attachments == []
