import subprocess
from pathlib import Path

from whyline_relay import config, preflight, setup


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
    answers = iter(["grok", "codex", "", ""])
    result = setup.run_role_wizard(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result == {
        "implementer": "grok",
        "tester": "codex",
        "reviewer": "claude",
        "backup": "",
    }
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
    answers = iter(["codex", "claude", "claude", ""])
    setup.run_role_wizard(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    loaded = config.load(tmp_path)
    assert loaded.pipeline is not None
    assert set(loaded.pipeline.stages) == {"draft", "test", "review"}


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    (root / "plan.md").write_text("- [ ] TASK-1: x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def test_run_commits_setup_before_running_doctor(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    answers = iter(["existing", "codex", "claude", "claude", "", "n"])
    seen_dirty_at_doctor_time = []

    def fake_runner(*a, **k):
        # doctor's own login-status checks call this; report "logged in"
        return subprocess.CompletedProcess(a, 0, "", "")

    def fake_preflight_run(root, *a, **k):
        from whyline_relay import gitcheck

        seen_dirty_at_doctor_time.append(gitcheck.is_dirty(root))
        return [preflight.Check("ok", "all good")]

    monkeypatch.setattr(setup.preflight, "run", fake_preflight_run)
    code = setup.run(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
        exec_fn=lambda binary, argv: (_ for _ in ()).throw(
            AssertionError("should not start -- test answers 'n'")
        ),
        which=lambda name: "/bin/x",
        runner=fake_runner,
    )
    assert seen_dirty_at_doctor_time == [False]
    assert code == 0


def test_run_refuses_to_offer_start_on_a_fail(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    answers = iter(["existing", "codex", "claude", "claude", ""])

    def fake_preflight_run(root, *a, **k):
        return [preflight.Check("FAIL", "grok is not on PATH", "install grok")]

    monkeypatch.setattr(setup.preflight, "run", fake_preflight_run)
    code = setup.run(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
        exec_fn=lambda binary, argv: (_ for _ in ()).throw(
            AssertionError("should never be offered on a FAIL")
        ),
        which=lambda name: "/bin/x",
    )
    assert code == 1

def test_run_asks_before_proceeding_on_a_warn_and_honors_no(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    answers = iter(["existing", "codex", "claude", "claude", "", "n"])

    def fake_preflight_run(root, *a, **k):
        return [preflight.Check("warn", "grok has no login check")]

    monkeypatch.setattr(setup.preflight, "run", fake_preflight_run)
    code = setup.run(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
        exec_fn=lambda binary, argv: (_ for _ in ()).throw(
            AssertionError("declined -- must not exec")
        ),
        which=lambda name: "/bin/x",
    )
    assert code == 0


def test_run_execs_into_start_when_clean_and_confirmed(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    answers = iter(["existing", "codex", "claude", "claude", "", ""])  # backup, then [Y]

    def fake_preflight_run(root, *a, **k):
        return [preflight.Check("ok", "all good")]

    monkeypatch.setattr(setup.preflight, "run", fake_preflight_run)
    calls = []
    code = setup.run(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
        exec_fn=lambda binary, argv: calls.append((binary, argv)),
        which=lambda name: "/bin/x",
    )
    assert calls == [("whyline-relay", ["whyline-relay", "start"])]
    assert code == 0


def test_run_role_wizard_backup_chain_writes_backup_table(tmp_path: Path):
    answers = iter(["codex", "claude", "claude", "aider, backup2"])
    result = setup.run_role_wizard(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["backup"] == "aider, backup2"
    config_text = (tmp_path / ".whyline" / "relay" / "config.toml").read_text()
    assert "[backup]" in config_text
    assert 'chain = ["aider", "backup2"]' in config_text


def test_run_role_wizard_blank_backup_writes_no_backup_table(tmp_path: Path):
    answers = iter(["codex", "claude", "claude", ""])
    result = setup.run_role_wizard(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["backup"] == ""
    config_text = (tmp_path / ".whyline" / "relay" / "config.toml").read_text()
    assert "[backup]" not in config_text


def test_run_role_wizard_backup_chain_config_loads_correctly(tmp_path: Path):
    answers = iter(["codex", "claude", "claude", "claude"])
    setup.run_role_wizard(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    loaded = config.load(tmp_path)
    assert loaded.backup_chain == ["claude"]


def test_run_stops_early_when_no_plan_source_resolved(tmp_path: Path):
    answers = iter(["existing"])  # no plan.md exists, "existing" is refused
    code = setup.run(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
        exec_fn=lambda binary, argv: (_ for _ in ()).throw(
            AssertionError("must not reach the wizard with no plan")
        ),
        which=lambda name: "/bin/x",
    )
    assert code == 1


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def test_choose_plan_source_brainstorm_end_to_end_writes_a_real_plan(tmp_path: Path):
    _init_git_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        from whyline_relay import brainstorm
        # chat.run_turn prepends earlier turns, so match the instruction
        # that was just sent, not a phrase that only appeared in history.
        current = prompt.rsplit("\n\n", 1)[-1]
        if "checkbox format" in current:
            brainstorm.plan_draft_path(tmp_path).write_text(
                "- [ ] T-1: build it\n  as synthesized.\n"
            )
        elif "Research" in current:
            brainstorm.temp_path(tmp_path, "claude").parent.mkdir(
                parents=True, exist_ok=True
            )
            brainstorm.temp_path(tmp_path, "claude").write_text("some findings\n")
        elif "Final Synthesis" in current:
            pass  # the final-synthesis turn itself needs no file write here
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    answers = iter([
        "brainstorm",   # choose_plan_source's own choice
        "build a widget",  # topic
        "1",            # models: claude only
        "0",            # passes: 0 (straight to synthesis)
        "",             # final model: default (claude)
        "a",            # review_gate: approve
        "n",            # don't start now
    ])
    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
        run_fn=fake_run_fn,
    )
    assert result is True
    written = (tmp_path / settings.plan).read_text(encoding="utf-8")
    assert "build it" in written
    from whyline_relay import plan
    assert len(plan.parse(written)) == 1


def test_choose_plan_source_brainstorm_declined_at_availability_check(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter([
        "brainstorm", "build a widget", "4", "0", "", "n",  # "4" = grok, unavailable here; "n" declines
    ])
    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result is False


def test_choose_plan_source_brainstorm_nothing_to_synthesize_is_reported(
    tmp_path: Path, capsys
):
    _init_git_repo(tmp_path)
    settings = config.load(tmp_path)

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        from whyline_relay import brainstorm
        # Every model writes nothing. merge_pass_zero still leaves a
        # title-only shared doc; drop that stub on the final-synthesis
        # turn so there is nothing to turn into a plan.
        current = prompt.rsplit("\n\n", 1)[-1]
        if "Final Synthesis" in current:
            shared = brainstorm.shared_path(tmp_path, "build a widget")
            if shared.exists():
                shared.unlink()
        return RunResult(0, '{"type":"result","result":"ok"}\n')

    printed = []
    answers = iter(["brainstorm", "build a widget", "1", "0", ""])
    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=fake_run_fn,
    )
    assert result is False
    assert any("nothing to turn into a plan" in line for line in printed)
