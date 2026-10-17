import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

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
    answers = iter(["codex", "claude", "claude", "grok, antigravity"])
    result = setup.run_role_wizard(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["backup"] == "grok, antigravity"
    config_text = (tmp_path / ".whyline" / "relay" / "config.toml").read_text()
    assert "[backup]" in config_text
    assert 'chain = ["grok", "antigravity"]' in config_text


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
            "",             # timeout default (15m)
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
    # Recipes install a Grok command in every repo. Drop that entry so model
    # "4" is unconfigured, and refuse any turn so a real binary cannot start.
    loaded = config.load(tmp_path)
    settings = replace(
        loaded,
        agents={
            name: command
            for name, command in loaded.agents.items()
            if name != "grok"
        },
    )
    answers = iter([
        "brainstorm",  # choose_plan_source
        "build a widget",  # topic
        "4",  # Grok
        "0",  # passes
        "",  # final model: the selected Grok
        "",  # timeout default
        "n",  # decline to proceed without it
    ])
    prompts: list[str] = []

    def answer(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(answers)

    def refuse_launch(*_args, **_kwargs):
        raise AssertionError("availability decline must not launch an agent")

    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=answer,
        print_fn=lambda *a, **k: None,
        run_fn=refuse_launch,
    )
    assert result is False
    assert any(
        "Grok" in prompt and "not configured for chat" in prompt
        for prompt in prompts
    )


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
    answers = iter(["brainstorm", "build a widget", "1", "0", "", ""])
    result = setup.choose_plan_source(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        run_fn=fake_run_fn,
    )
    assert result is False
    assert any("No selected agent succeeded" in line for line in printed)


_RICH_CONFIG = """\
timeout_minutes = 17
plan = "docs/plan.md"
max_rounds = 9
branch_prefix = "work/"

[roles]
implementer = "codex"
tester      = "claude"
reviewer    = "claude"

[planner]
draft = "codex"
review = "grok"
max_visits = 4

[pipeline]
default_profile = "full"

[pipeline.profiles]
full = ["draft", "review"]

[pipeline.stages.draft]
role   = "implementer"
prompt = "implement"
[pipeline.stages.draft.on]
ready = "@next"

[pipeline.stages.review]
role   = "reviewer"
prompt = "review"
[pipeline.stages.review.on]
approved = "@complete"

[agents.aider]
adapter = "generic"
command = ["aider", "--yes"]

[backup]
chain = ["claude"]
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def _relay_config(root: Path) -> Path:
    return root / ".whyline" / "relay" / "config.toml"


def _without_backup(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == "[backup]":
            index += 1
            while index < len(lines) and not lines[index].lstrip().startswith("["):
                index += 1
            continue
        kept.append(lines[index])
        index += 1
    return "\n".join(kept).rstrip("\n") + "\n"


def _repo_with_config(tmp_path: Path, text: str = _RICH_CONFIG) -> Path:
    _init_repo(tmp_path)
    path = _relay_config(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(text)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "config")
    return tmp_path


def test_read_backup_missing_file_is_empty(tmp_path: Path):
    _init_repo(tmp_path)
    assert not _relay_config(tmp_path).exists()
    assert setup.read_backup(tmp_path) == []


def test_read_backup_bad_chain_name_raises_config_error(tmp_path: Path):
    repo = _repo_with_config(
        tmp_path, _RICH_CONFIG.replace('["claude"]', '["not-an-agent"]')
    )
    with pytest.raises(config.ConfigError, match="not-an-agent"):
        setup.read_backup(repo)


def test_read_backup_broken_file_raises_config_error(tmp_path: Path):
    repo = _repo_with_config(tmp_path, "timeout_minutes = [\n")
    with pytest.raises(config.ConfigError, match="could not read"):
        setup.read_backup(repo)


def _outside_planner(text: str) -> str:
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == "[planner]":
            skipping = True
            continue
        if skipping and line.lstrip().startswith("["):
            skipping = False
        if not skipping:
            kept.append(line)
    return "".join(kept)


def test_write_planner_sets_test_and_leaves_every_other_table(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    path = _relay_config(repo)
    before = path.read_text()
    setup.write_planner(repo, "claude", "codex", "claude")
    after = path.read_text()
    assert config.load(repo).planner == config.PlannerConfig(
        draft="claude", review="codex", max_visits=4, test="claude"
    )
    assert _outside_planner(after) == _outside_planner(before)
    assert 'test = "claude"' in after
    assert "max_visits = 4" in after
    assert "[pipeline]\n" in after
    assert "[backup]\n" in after
    assert "[agents.aider]\n" in after
    assert _git(repo, "log", "-1", "--format=%s") == (
        "setup: plans drafted by claude, tested by claude, reviewed by codex\n"
    )


def test_write_planner_empty_test_writes_an_empty_key(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    setup.write_planner(repo, "claude", "codex", "")
    text = _relay_config(repo).read_text()
    assert config.load(repo).planner.test == ""
    assert 'test = ""' in text
    assert "max_visits = 4" in text
    assert _git(repo, "log", "-1", "--format=%s") == (
        "setup: plans drafted by claude, reviewed by codex\n"
    )


def test_write_backup_changes_the_chain_and_leaves_other_tables(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    path = _relay_config(repo)
    before = path.read_text()
    written = setup.write_backup(repo, ["codex", "", "grok"])
    after = path.read_text()
    assert written == path
    assert setup.read_backup(repo) == ["codex", "grok"]
    assert _without_backup(after) == _without_backup(before)
    assert 'tester      = "claude"' in after
    assert "[planner]\n" in after
    assert 'review = "grok"' in after
    assert "[pipeline]\n" in after
    assert "[agents.aider]\n" in after
    assert "timeout_minutes = 17\n" in after
    assert 'plan = "docs/plan.md"\n' in after
    assert "max_rounds = 9\n" in after
    assert 'branch_prefix = "work/"\n' in after
    assert after.endswith('[backup]\nchain = ["codex", "grok"]\n')
    assert _git(repo, "log", "-1", "--format=%s") == "setup: backup chain codex, grok\n"


def test_write_backup_commit_lists_only_config_toml(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    (repo / "README.md").write_text("changed\n")
    (repo / "other.txt").write_text("leave me\n")
    setup.write_backup(repo, ["codex", "grok"])
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == [".whyline/relay/config.toml"]
    status = _git(repo, "status", "--porcelain")
    assert "README.md" in status
    assert "other.txt" in status
    assert "config.toml" not in status


def test_write_backup_empty_chain_removes_the_table(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    path = _relay_config(repo)
    before = path.read_text()
    setup.write_backup(repo, [])
    after = path.read_text()
    assert "[backup]" not in after
    assert setup.read_backup(repo) == []
    assert after == _without_backup(before)
    assert _git(repo, "log", "-1", "--format=%s") == "setup: backup chain none\n"


def test_write_backup_same_chain_does_not_commit_again(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    setup.write_backup(repo, ["codex", "grok"])
    first = _git(repo, "rev-parse", "HEAD")
    setup.write_backup(repo, ["codex", "grok"])
    assert _git(repo, "rev-parse", "HEAD") == first
    assert _git(repo, "status", "--porcelain") == ""


def test_write_backup_rejected_name_leaves_the_file_unchanged(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    path = _relay_config(repo)
    before = path.read_bytes()
    head = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(config.ConfigError, match="not-an-agent"):
        setup.write_backup(repo, ["not-an-agent"])
    assert path.read_bytes() == before
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert setup.read_backup(repo) == ["claude"]


def test_write_backup_commit_false_writes_without_committing(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    setup.write_backup(repo, ["grok"], commit=False)
    assert _git(repo, "rev-parse", "HEAD") == head
    assert setup.read_backup(repo) == ["grok"]
    assert "config.toml" in _git(repo, "status", "--porcelain")


def test_write_backup_missing_file_and_empty_chain_creates_nothing(tmp_path: Path):
    _init_repo(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD")
    written = setup.write_backup(tmp_path, ["", ""])
    assert written == _relay_config(tmp_path)
    assert not written.exists()
    assert not (tmp_path / ".whyline").exists()
    assert _git(tmp_path, "rev-parse", "HEAD") == head


def test_write_backup_then_write_roles_leaves_a_single_chain_table(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    setup.write_backup(repo, ["grok"], commit=False)
    setup.write_roles(repo, "codex", "claude", "claude", ["codex"])
    text = _relay_config(repo).read_text()
    assert text.count("[backup]") == 1
    assert text.endswith('[backup]\nchain = ["codex"]\n')
    assert setup.read_backup(repo) == ["codex"]
    assert "[planner]\n" in text
    assert "[pipeline]\n" in text
    assert "[agents.aider]\n" in text
    assert 'tester      = "claude"' in text
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == [
        ".whyline/relay/config.toml",
        ".whyline/relay/prompts/test.md",
    ]
    assert _git(repo, "log", "-1", "--format=%s") == (
        "setup: assign implementer/tester/reviewer roles\n"
    )


def test_write_roles_default_chain_removes_backup_from_a_pipeline_config(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    setup.write_backup(repo, ["grok"], commit=False)
    setup.write_roles(repo, "codex", "claude", "claude")
    text = _relay_config(repo).read_text()
    assert "[backup]" not in text
    assert setup.read_backup(repo) == []
    assert "[pipeline]\n" in text
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == [
        ".whyline/relay/config.toml",
        ".whyline/relay/prompts/test.md",
    ]


def test_write_backup_unstaged_config_edits_ride_along(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    path = _relay_config(repo)
    path.write_text(path.read_text().replace(
        "timeout_minutes = 17", "timeout_minutes = 18"
    ))
    (repo / "other.txt").write_text("leave me\n")
    setup.write_backup(repo, ["codex"])
    committed = _git(repo, "show", "HEAD:.whyline/relay/config.toml")
    assert "timeout_minutes = 18\n" in committed
    assert 'chain = ["codex"]\n' in committed
    assert 'tester      = "claude"' in committed
    status = _git(repo, "status", "--porcelain")
    assert "other.txt" in status
    assert "config.toml" not in status


def _without_timeout_line(text: str) -> str:
    return "".join(
        line for line in text.splitlines(keepends=True)
        if not line.lstrip().startswith("timeout_minutes")
    )


def test_write_timeout_minutes_replaces_existing_line_with_unquoted_zero(tmp_path: Path):
    repo = _repo_with_config(tmp_path)
    path = _relay_config(repo)
    before = path.read_text()
    written = setup.write_timeout_minutes(repo, 0)
    after = path.read_text()
    assert written == path
    assert after == before.replace("timeout_minutes = 17\n", "timeout_minutes = 0\n")
    assert 'timeout_minutes = "0"' not in after
    assert _without_timeout_line(after) == _without_timeout_line(before)
    assert config.load(repo).timeout_minutes == 0
    assert _git(repo, "log", "-1", "--format=%s") == "setup: timeout 0 minutes\n"


def test_write_timeout_minutes_inserts_unquoted_zero_before_the_first_table(tmp_path: Path):
    repo = _repo_with_config(
        tmp_path, _RICH_CONFIG.replace("timeout_minutes = 17\n", "")
    )
    path = _relay_config(repo)
    before = path.read_text()
    assert "timeout_minutes" not in before
    written = setup.write_timeout_minutes(repo, 0)
    after = path.read_text()
    assert written == path
    assert "timeout_minutes = 0\n" in after
    assert 'timeout_minutes = "' not in after
    assert after.index("timeout_minutes = 0\n") < after.index("[roles]")
    assert _without_timeout_line(after) == before
    assert config.load(repo).timeout_minutes == 0
