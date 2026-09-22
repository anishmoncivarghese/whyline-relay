from pathlib import Path

from whyline_relay import prompts


GOLDEN = Path(__file__).parent / "golden"
KW = dict(
    task_id="T-1",
    task_text="T-1: Do it\nwith detail",
    sync_packet="PACKET",
    round_=2,
    review_feedback="fix the edge",
)


def test_default_review_prompt_still_renders_byte_for_byte_as_0_2_1():
    """REVIEW hasn't changed since 0.2.1. IMPLEMENT has, deliberately, in 0.2.3 —

    see test_default_implement_prompt_matches_its_current_golden below.
    """
    assert prompts.render(prompts.REVIEW, **KW) == (
        GOLDEN / "prompt_review_0.2.1.txt"
    ).read_text(encoding="utf-8")


def test_default_implement_prompt_matches_its_current_golden():
    """Guards against an accidental change to IMPLEMENT between releases.

    Regenerate this golden deliberately (see docs/releases/) whenever the
    default implement prompt's wording is meant to change, as it did in 0.2.3
    to forbid backgrounded commands (test_prompts.py has the content check).
    """
    assert prompts.render(prompts.IMPLEMENT, **KW) == (
        GOLDEN / "prompt_implement_0.2.3.txt"
    ).read_text(encoding="utf-8")


def test_other_roles_change_only_the_names():
    implement = prompts.render(
        prompts.IMPLEMENT, implementer="aider", reviewer="gemini", **KW
    )
    assert "--from aider --to gemini --status ready-for-review" in implement
    assert "--actor aider --role implementer" in implement
    review = prompts.render(
        prompts.REVIEW, implementer="aider", reviewer="gemini", **KW
    )
    assert "--from gemini --to aider --status changes-requested" in review
    assert review.count("--from gemini --to gemini") == 2
    assert "codex" not in implement and "claude" not in review


def test_role_placeholders_in_task_text_are_not_rewritten():
    text = prompts.render(
        prompts.IMPLEMENT, **{**KW, "task_text": "uses {implementer} literally"}
    )
    assert "uses {implementer} literally" in text
