from dataclasses import replace
import subprocess
from pathlib import Path

import pytest

from whyline_relay import brainstorm, config, plan, planner, testplan


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


VALID_PLAN = "- [ ] T-1: do X\n  detail.\n"
VALID_TESTS = (
    "- [ ] TP-1: X works\n"
    "  covers: T-1\n"
    "  kind: auto\n"
    "  steps:\n"
    "    - run it\n"
    "  expected:\n"
    "    - it works\n"
)
THREE_CRITERIA = (
    "The change is successful when:\n"
    "- the cache evicts the oldest key\n"
    "- a missing key returns None\n"
    "- a concurrent write does not corrupt the map\n"
)


def _logged_in(*args, **_kwargs):
    """A successful draft still runs the agent's login check. Keep it fake
    so a logged-out machine does not fail the draft over to the backup."""
    return subprocess.CompletedProcess(args, 0, "", "")


def _write_shared_doc(root: Path, topic: str, synthesis: str = "do X then Y") -> None:
    shared = brainstorm.shared_path(root, topic)
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(
        f"# Brainstorm: {topic}\n\n## Final Synthesis\n\n{synthesis}\n"
    )


def _settings(
    root: Path, *, test: str = "", backup_chain: list[str] | None = None
) -> config.Config:
    settings = config.load(root)
    updates = {}
    if test:
        updates["planner"] = replace(settings.planner, test=test)
    if backup_chain is not None:
        updates["backup_chain"] = backup_chain
        extra_agents = {
            name: [name] for name in backup_chain if name not in settings.agents
        }
        if extra_agents:
            updates["agents"] = {**settings.agents, **extra_agents}
            updates["adapters"] = {
                **settings.adapters,
                **{name: "generic" for name in extra_agents},
            }
    return replace(settings, **updates) if updates else settings


def test_generate_plan_from_synthesis_succeeds_first_try(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        brainstorm.plan_draft_path(tmp_path).write_text(
            "- [ ] T-1: do X\n  detail.\n- [ ] T-2: do Y\n  detail.\n"
        )
        return RunResult(0, '{"type":"result","result":"done"}\n')

    result = brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", models, "my topic", run_fn=fake_run_fn,
    )
    assert result == brainstorm.plan_draft_path(tmp_path)
    assert len(plan.parse(result.read_text(encoding="utf-8"))) == 2


def test_generate_plan_from_synthesis_retries_once_on_a_parse_error(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]
    attempts = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        attempts.append(prompt)
        if len(attempts) == 1:
            brainstorm.plan_draft_path(tmp_path).write_text("not a real plan\n")
        else:
            brainstorm.plan_draft_path(tmp_path).write_text(
                "- [ ] T-1: do X\n  detail.\n"
            )
        return RunResult(0, '{"type":"result","result":"done"}\n')

    result = brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", models, "my topic", run_fn=fake_run_fn,
    )
    assert len(attempts) == 2
    assert "did not parse" in attempts[1]
    assert len(plan.parse(result.read_text(encoding="utf-8"))) == 1


def test_generate_plan_from_synthesis_raises_after_exhausting_retries(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        brainstorm.plan_draft_path(tmp_path).write_text("not a real plan\n")
        return RunResult(0, '{"type":"result","result":"done"}\n')

    with pytest.raises(plan.PlanError):
        brainstorm.generate_plan_from_synthesis(
            tmp_path, settings, "claude", models, "my topic", run_fn=fake_run_fn,
        )
    assert brainstorm.plan_draft_path(tmp_path).exists()


def test_generate_plan_from_synthesis_refuses_when_nothing_to_synthesize(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude")]
    with pytest.raises(brainstorm.NothingToSynthesize):
        brainstorm.generate_plan_from_synthesis(
            tmp_path, settings, "claude", models, "a topic with no doc",
        )


def test_generate_plan_from_synthesis_propagates_agent_missing_without_retrying(
    tmp_path: Path,
):
    from whyline_relay import agents

    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]
    attempts = []

    def fake_run_fn(command, prompt, **kwargs):
        attempts.append(1)
        raise agents.AgentMissing("claude is not installed")

    with pytest.raises(agents.AgentMissing):
        brainstorm.generate_plan_from_synthesis(
            tmp_path, settings, "claude", models, "my topic", run_fn=fake_run_fn,
        )
    assert len(attempts) == 1


def test_generate_plan_from_synthesis_includes_feedback_when_revising(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]
    prompts_seen = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        prompts_seen.append(prompt)
        brainstorm.plan_draft_path(tmp_path).write_text("- [ ] T-1: x\n  y.\n")
        return RunResult(0, '{"type":"result","result":"done"}\n')

    brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", models, "my topic",
        feedback="make it shorter", run_fn=fake_run_fn,
    )
    assert "make it shorter" in prompts_seen[0]


def test_generate_plan_from_synthesis_writes_the_test_plan_beside_the_draft(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    settings = _settings(tmp_path, test="codex")
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        if "Write a test plan at" in prompt:
            testplan.path_for(brainstorm.plan_draft_path(tmp_path)).write_text(
                VALID_TESTS
            )
        else:
            brainstorm.plan_draft_path(tmp_path).write_text(VALID_PLAN)
        return RunResult(0, '{"type":"result","result":"done"}\n')

    result = brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", models, "my topic", run_fn=fake_run_fn,
    )
    draft = brainstorm.plan_draft_path(tmp_path)
    tests = testplan.path_for(draft)
    assert result == draft
    assert tests == draft.with_name("brainstorm-plan-draft.tests.md")
    assert tests.read_text(encoding="utf-8") == VALID_TESTS
    assert testplan.validate(tests.read_text(encoding="utf-8"), draft.read_text()) == []


def test_generate_plan_from_synthesis_lists_every_acceptance_criterion_in_the_test_prompt(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    settings = _settings(tmp_path, test="codex")
    _write_shared_doc(tmp_path, "my topic", THREE_CRITERIA)
    models = [("claude", "Claude")]
    prompts_seen = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        prompts_seen.append(prompt)
        if "Write a test plan at" in prompt:
            testplan.path_for(brainstorm.plan_draft_path(tmp_path)).write_text(
                VALID_TESTS
            )
        else:
            brainstorm.plan_draft_path(tmp_path).write_text(VALID_PLAN)
        return RunResult(0, '{"type":"result","result":"done"}\n')

    brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", models, "my topic", run_fn=fake_run_fn,
    )
    assert len(prompts_seen) == 2
    test_prompt = prompts_seen[1]
    assert "the cache evicts the oldest key" in test_prompt
    assert "a missing key returns None" in test_prompt
    assert "a concurrent write does not corrupt the map" in test_prompt
    assert "at least one scenario" in test_prompt


def test_generate_plan_from_synthesis_skips_the_test_turn_when_test_is_empty(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    assert settings.planner.test == ""
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        calls.append(command[0])
        brainstorm.plan_draft_path(tmp_path).write_text(VALID_PLAN)
        return RunResult(0, '{"type":"result","result":"done"}\n')

    brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", models, "my topic", run_fn=fake_run_fn,
    )
    assert calls == ["claude"]
    assert not testplan.path_for(brainstorm.plan_draft_path(tmp_path)).exists()


def test_a_failing_test_planner_falls_through_to_the_backup(tmp_path: Path):
    _init_repo(tmp_path)
    settings = _settings(tmp_path, test="codex", backup_chain=["aider"])
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]
    agents_seen = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        agents_seen.append(command[0])
        if "Write a test plan at" in prompt:
            if command[0] == "codex":
                return RunResult(
                    1, "You have exceeded your usage limit. Try again later."
                )
            testplan.path_for(brainstorm.plan_draft_path(tmp_path)).write_text(
                VALID_TESTS
            )
            return RunResult(0, '{"type":"result","result":"done"}\n')
        brainstorm.plan_draft_path(tmp_path).write_text(VALID_PLAN)
        return RunResult(0, '{"type":"result","result":"done"}\n')

    result = brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", models, "my topic",
        run_fn=fake_run_fn, runner=_logged_in,
    )
    assert agents_seen == ["claude", "codex", "aider"]
    tests = testplan.path_for(result)
    assert tests.read_text(encoding="utf-8") == VALID_TESTS
    assert testplan.validate(tests.read_text(encoding="utf-8"), result.read_text()) == []


def test_an_exhausted_test_planner_chain_keeps_the_draft_and_reports_no_test_plan(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    settings = _settings(tmp_path, test="codex", backup_chain=["aider"])
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]
    reported = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        if "Write a test plan at" in prompt:
            return RunResult(1, "You have exceeded your usage limit. Try again later.")
        brainstorm.plan_draft_path(tmp_path).write_text(VALID_PLAN)
        return RunResult(0, '{"type":"result","result":"done"}\n')

    result = brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", models, "my topic",
        run_fn=fake_run_fn, runner=_logged_in, print_fn=reported.append,
    )
    draft = brainstorm.plan_draft_path(tmp_path)
    assert result == draft
    assert draft.read_text(encoding="utf-8") == VALID_PLAN
    assert not testplan.path_for(draft).exists()
    assert any("no test plan was written" in line.lower() for line in reported)


def test_an_exhausted_test_planner_does_not_reuse_a_stale_valid_test_plan(
    tmp_path: Path,
):
    _init_repo(tmp_path)
    settings = _settings(tmp_path, test="codex", backup_chain=["aider"])
    _write_shared_doc(tmp_path, "my topic")
    models = [("claude", "Claude")]
    draft = brainstorm.plan_draft_path(tmp_path)
    stale = testplan.path_for(draft)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(VALID_TESTS)
    reported = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        if "Write a test plan at" in prompt:
            return RunResult(
                1, "You have exceeded your usage limit. Try again later."
            )
        draft.write_text(VALID_PLAN)
        return RunResult(0, '{"type":"result","result":"done"}\n')

    result = brainstorm.generate_plan_from_synthesis(
        tmp_path, settings, "claude", models, "my topic",
        run_fn=fake_run_fn, runner=_logged_in, print_fn=reported.append,
    )
    assert result == draft
    assert draft.read_text(encoding="utf-8") == VALID_PLAN
    assert not stale.exists()
    assert any("no test plan was written" in line.lower() for line in reported)
    with pytest.raises(plan.PlanError, match="no test plan"):
        planner.approve(tmp_path, settings, draft, drafted_by="claude")
