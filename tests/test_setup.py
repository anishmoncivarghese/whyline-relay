from pathlib import Path

from whyline_relay import config, setup


def test_choose_plan_source_existing_default_when_plan_exists(tmp_path: Path):
    (tmp_path / "plan.md").write_text("- [ ] TASK-1: x\n")
    settings = config.load(tmp_path)
    answers = iter([""])  # accept the bracketed default: existing
    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result is True


def test_choose_plan_source_refuses_when_no_plan_and_existing_chosen(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["existing"])
    printed = []
    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    assert result is False
    assert any("does not exist" in line for line in printed)


def test_choose_plan_source_draft_calls_planner_start(tmp_path: Path):
    settings = config.load(tmp_path)
    calls = []

    def fake_planner_start(root, settings_arg, description):
        calls.append((root, description))
        (root / settings_arg.plan).write_text("- [ ] TASK-1: drafted\n")
        return "drafted!"

    answers = iter(["draft", "build a widget"])
    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
        planner_start=fake_planner_start,
    )
    assert result is True
    assert calls == [(tmp_path, "build a widget")]


def test_choose_plan_source_draft_with_no_description_does_nothing(tmp_path: Path):
    settings = config.load(tmp_path)
    calls = []
    answers = iter(["draft", ""])
    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
        planner_start=lambda root, s, d: calls.append(d),
    )
    assert result is False
    assert calls == []


def test_choose_plan_source_draft_handles_a_plan_already_in_progress(tmp_path: Path):
    from whyline_relay import planner

    settings = config.load(tmp_path)

    def fake_planner_start(root, settings_arg, description):
        raise planner.PlanAlreadyInProgress("a plan draft is already in progress")

    answers = iter(["draft", "build a widget"])
    printed = []
    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        planner_start=fake_planner_start,
    )
    assert result is False
    assert any("already in progress" in line for line in printed)


def test_run_role_wizard_writes_config_and_test_prompt(tmp_path: Path):
    answers = iter(["grok", "codex", ""])
    result = setup.run_role_wizard(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result == {"implementer": "grok", "tester": "codex", "reviewer": "claude"}
    config_text = (tmp_path / ".whyline" / "relay" / "config.toml").read_text()
    assert 'implementer = "grok"' in config_text
    assert 'tester      = "codex"' in config_text
    assert 'reviewer    = "claude"' in config_text
    assert "[pipeline.stages.test]" in config_text
    assert 'default_profile = "full"' in config_text
    test_prompt = (tmp_path / ".whyline" / "relay" / "prompts" / "test.md").read_text()
    assert "You are the tester for this task" in test_prompt
    assert "\x7bactor\x7d" in test_prompt
    assert "{tester}" not in test_prompt  # no such placeholder exists in render()


def test_run_role_wizard_config_parses_as_a_valid_pipeline(tmp_path: Path):
    # The wizard's own output must be immediately usable by config.load --
    # not just plausible-looking TOML. Uses only built-in agents so this
    # exercises the happy path; an unconfigured name is Task 2's concern
    # (doctor correctly reports it as a FAIL rather than crashing).
    answers = iter(["codex", "claude", "claude"])
    setup.run_role_wizard(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    loaded = config.load(tmp_path)
    assert loaded.pipeline is not None
    assert set(loaded.pipeline.stages) == {"draft", "test", "review"}
