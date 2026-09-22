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


def test_default_roles_render_the_0_2_1_prompts_byte_for_byte():
    assert prompts.render(prompts.IMPLEMENT, **KW) == (
        GOLDEN / "prompt_implement_0.2.1.txt"
    ).read_text(encoding="utf-8")
    assert prompts.render(prompts.REVIEW, **KW) == (
        GOLDEN / "prompt_review_0.2.1.txt"
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
