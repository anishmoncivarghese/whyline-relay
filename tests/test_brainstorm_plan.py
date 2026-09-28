import subprocess
from pathlib import Path

import pytest

from whyline_relay import brainstorm, config, plan


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def _write_shared_doc(root: Path, topic: str) -> None:
    shared = brainstorm.shared_path(root, topic)
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(
        f"# Brainstorm: {topic}\n\n## Final Synthesis\n\ndo X then Y\n"
    )


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
