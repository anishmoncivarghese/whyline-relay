from pathlib import Path

from whyline_relay import prompts


def test_render_substitutes_every_placeholder():
    rendered = prompts.render(
        "{sync_packet}|{task_id}|{task_text}|{round}|{review_feedback}",
        task_id="WL-1",
        task_text="WL-1: Do the thing",
        sync_packet="PACKET",
        round_=2,
        review_feedback="tests are missing",
    )
    assert rendered == "PACKET|WL-1|WL-1: Do the thing|2|tests are missing"


def test_render_leaves_json_braces_alone():
    rendered = prompts.render(
        'use {"permissions": {"allow": []}} for {task_id}',
        task_id="WL-1",
        task_text="",
        sync_packet="",
        round_=1,
        review_feedback="",
    )
    assert '{"permissions": {"allow": []}}' in rendered


def test_builtin_templates_use_only_known_placeholders():
    import re

    for template in (prompts.IMPLEMENT, prompts.REVIEW):
        for found in re.findall(r"\{([a-z_]+)\}", template):
            assert found in prompts.PLACEHOLDERS, found


def test_implement_template_names_the_exact_handoff_command():
    assert "whyline handoff {task_id} --from codex --to claude" in prompts.IMPLEMENT
    assert "--status ready-for-review" in prompts.IMPLEMENT
    assert "whyline note" in prompts.IMPLEMENT


def test_review_template_names_both_permitted_outcomes():
    assert "--status approved" in prompts.REVIEW
    assert "--to codex --status changes-requested" in prompts.REVIEW


def test_no_template_ever_suggests_a_bypass_flag():
    for template in (prompts.IMPLEMENT, prompts.REVIEW):
        assert "dangerously" not in template
        assert "git push" not in template


def test_load_prefers_a_user_template(tmp_path: Path):
    target = tmp_path / ".whyline" / "relay" / "prompts"
    target.mkdir(parents=True)
    (target / "implement.md").write_text("MINE {task_id}")
    assert prompts.load(tmp_path, "implement") == "MINE {task_id}"


def test_load_falls_back_to_the_builtin(tmp_path: Path):
    assert prompts.load(tmp_path, "review") == prompts.REVIEW
