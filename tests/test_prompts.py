from pathlib import Path

import pytest

from whyline_relay import pipeline as pipeline_module
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


def test_implement_template_forbids_backgrounded_commands():
    """Measured against a real headless agent (2026-09-22): a turn can end while a

    command it started is still running in the background, silently dropping the
    work. Telling it not to do that beat telling the task itself not to run
    anything, in a real side-by-side comparison (half the tokens, and it still
    ran the real tests instead of only reading files back).
    """
    assert "never run anything in the background" in prompts.IMPLEMENT
    assert "never\nend your turn while something you started is still running" in prompts.IMPLEMENT


def test_implement_template_names_the_exact_handoff_command():
    assert "whyline handoff {task_id} --from {implementer} --to {reviewer}" in prompts.IMPLEMENT
    assert "--status ready-for-review" in prompts.IMPLEMENT
    assert "whyline note" in prompts.IMPLEMENT


def test_review_template_names_both_permitted_outcomes():
    assert "--status approved" in prompts.REVIEW
    assert "--to {implementer} --status changes-requested" in prompts.REVIEW


def test_review_template_requires_independent_plain_test_command():
    assert "Run the project's tests yourself before approving" in prompts.REVIEW
    assert "`uv run pytest -q`" in prompts.REVIEW
    assert "environment-variable prefixes, pipes, or shell chains" in prompts.REVIEW


def test_review_template_blocks_when_test_command_is_denied():
    assert "If a test command is denied, do not approve" in prompts.REVIEW
    assert "--status blocked" in prompts.REVIEW
    assert "exact denied command" in prompts.REVIEW
    assert "permission to add" in prompts.REVIEW


def test_review_template_exempts_tasks_without_code_or_tests():
    assert "changes no code and has no tests is" in prompts.REVIEW
    assert "exemption applies in the handoff summary" in prompts.REVIEW


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


def test_load_raises_a_clear_error_for_an_unknown_prompt_with_no_override(tmp_path):
    with pytest.raises(prompts.PromptError, match="tester"):
        prompts.load(tmp_path, "tester")


def test_load_finds_a_user_override_for_a_custom_stage_name(tmp_path):
    prompt_dir = prompts.prompts_dir(tmp_path)
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "tester.md").write_text(
        "Test {task_id} as {actor}, stage {stage}."
    )
    assert (
        prompts.load(tmp_path, "tester")
        == "Test {task_id} as {actor}, stage {stage}."
    )


def test_render_substitutes_the_new_placeholders():
    rendered = prompts.render(
        "You are {actor}, role {role}, on stage {stage} of profile {profile}.",
        task_id="T-1",
        task_text="x",
        sync_packet="",
        round_=1,
        review_feedback="",
        actor="claude-fast",
        role="tester",
        stage="test",
        profile="full",
    )
    assert rendered == "You are claude-fast, role tester, on stage test of profile full."


def test_render_output_for_the_builtin_templates_is_unchanged():
    # New optional kwargs must not appear in output when the caller omits them --
    # the built-in IMPLEMENT/REVIEW templates never reference
    # {actor}/{role}/{stage}/{profile}.
    before = prompts.render(
        prompts.IMPLEMENT,
        task_id="T-1",
        task_text="do it",
        sync_packet="PACKET",
        round_=1,
        review_feedback="",
    )
    after = prompts.render(
        prompts.IMPLEMENT,
        task_id="T-1",
        task_text="do it",
        sync_packet="PACKET",
        round_=1,
        review_feedback="",
        actor="codex",
        role="implementer",
        stage="implement",
        profile="default",
    )
    assert before == after


def _three_stage_pipeline():
    return pipeline_module.Pipeline(
        roles={
            "implementer": pipeline_module.Role("implementer", agent="codex"),
            "tester": pipeline_module.Role("tester", agent="claude"),
            "reviewer": pipeline_module.Role("reviewer", agent="claude"),
        },
        stages={
            "draft": pipeline_module.Stage(
                "draft", "implementer", "implement", {"ready": "@next"}
            ),
            "test": pipeline_module.Stage(
                "test", "tester", "test", {"passed": "@next", "failed": "draft"}
            ),
            "review": pipeline_module.Stage(
                "review",
                "reviewer",
                "review",
                {"approved": "@complete", "rejected": "draft"},
            ),
        },
        profiles={
            "full": pipeline_module.Profile("full", ("draft", "test", "review")),
            "quick": pipeline_module.Profile("quick", ("draft", "review")),
        },
        default_profile="full",
    )


def test_stage_footer_lists_every_outcome_with_its_exact_recipient():
    pipe = _three_stage_pipeline()
    agents = {"implementer": "codex", "tester": "claude", "reviewer": "claude"}
    footer = prompts.stage_footer(
        pipe.stages["test"], pipe, "full", agents, "claude", "T-1"
    )
    assert "whyline handoff T-1 --from claude --to codex --status failed" in footer
    assert "whyline handoff T-1 --from claude --to claude --status passed" in footer
    assert "git commit" in footer  # the do-not-commit notice is always present


def test_stage_footer_resolves_next_relative_to_the_active_profile():
    pipe = _three_stage_pipeline()
    agents = {"implementer": "codex", "tester": "claude", "reviewer": "claude"}
    full = prompts.stage_footer(
        pipe.stages["draft"], pipe, "full", agents, "codex", "T-1"
    )
    quick = prompts.stage_footer(
        pipe.stages["draft"], pipe, "quick", agents, "codex", "T-1"
    )
    assert "stage 'test'" in full
    assert "stage 'test'" not in quick
    assert "stage 'review'" in quick


def test_stage_footer_states_complete_and_blocked_correctly():
    pipe = _three_stage_pipeline()
    agents = {"implementer": "codex", "tester": "claude", "reviewer": "claude"}
    footer = prompts.stage_footer(
        pipe.stages["review"], pipe, "full", agents, "claude", "T-1"
    )
    assert "whyline handoff T-1 --from claude --to claude --status approved" in footer
    assert "the task is finished" in footer
