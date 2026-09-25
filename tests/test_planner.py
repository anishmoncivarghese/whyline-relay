import json
import subprocess
import sys
from pathlib import Path

import pytest

from whyline_relay import config, loop, planner, state


FAKE = str(Path(__file__).parent / "fake_pipeline_agent.py")
DESCRIPTION = "add a health-check endpoint"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-m", "initial")
    (tmp_path / ".whyline").mkdir()
    monkeypatch.setattr(
        loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET"
    )
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    monkeypatch.setattr(planner.whylinecmd, "claim", lambda *a, **k: None)
    return tmp_path


def settings_with_planner(root: Path, max_visits=3) -> config.Config:
    base = config.load(root)
    return config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={"codex": ["codex"], "claude": ["claude"]},
        status_map=base.status_map,
        planner=config.PlannerConfig(
            draft="codex", review="claude", max_visits=max_visits
        ),
    )


def _scripted_run(specs: list[str]):
    def run(
        command,
        prompt,
        *,
        cwd,
        log_path,
        timeout_seconds,
        which=None,
        echo=True,
        agent_name=None,
    ):
        spec = specs.pop(0)
        result = subprocess.run(
            [sys.executable, FAKE, spec, str(cwd), prompt],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(result.stdout + result.stderr)
        return result.returncode

    return run


def _confirm(answers: list[str]):
    def confirm(prompt: str) -> str:
        return answers.pop(0)

    return confirm


def test_a_fresh_session_advances_from_draft_to_complete(repo, monkeypatch):
    settings = settings_with_planner(repo)
    # "claude:ready" -- the draft stage (agent codex) hands "ready" to whoever
    # fills "review" (claude); only @complete/@blocked are ever self-addressed.
    monkeypatch.setattr(
        loop.agents, "run", _scripted_run(["claude:ready", "claude:approved"])
    )
    planner._run_pipeline(repo, settings, DESCRIPTION, echo=False)
    saved = state.load_plan(repo)
    assert saved.stage == "@complete"


def test_a_review_revise_outcome_sends_it_back_to_draft(repo, monkeypatch):
    settings = settings_with_planner(repo)
    # Turn 1 (draft/codex) -> ready, addressed to review's agent (claude).
    # Turn 2 (review/claude) -> revise, addressed to draft's agent (codex).
    # Turn 3 (draft/codex) -> ready, addressed to claude again.
    # Turn 4 (review/claude) -> approved, self-addressed (@complete).
    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted_run(
            ["claude:ready", "codex:revise", "claude:ready", "claude:approved"]
        ),
    )
    planner._run_pipeline(repo, settings, DESCRIPTION, echo=False)
    saved = state.load_plan(repo)
    assert saved.stage == "@complete"
    # round starts at 1 and increments once per advance (not on the final
    # "complete" turn): draft(1) -> review(2) -> draft(3) -> review(4).
    assert saved.round == 4


def test_a_blocked_outcome_pauses_instead_of_completing(repo, monkeypatch):
    settings = settings_with_planner(repo)
    # blocked is always self-addressed, like @complete -- no recipient check
    # applies to it, so "codex:blocked" (draft blocking itself) is correct.
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["codex:blocked"]))
    with pytest.raises(loop.Paused, match="blocked"):
        planner._run_pipeline(repo, settings, DESCRIPTION, echo=False)


def test_exceeding_max_visits_pauses(repo, monkeypatch):
    settings = settings_with_planner(repo, max_visits=1)
    # Turn 1: draft -> ready -> review (stage_visits: draft=1, review=1, ok).
    # Turn 2: review -> revise -> draft (stage_visits: draft=2 > cap 1, raises
    # before a third turn is ever launched -- only 2 specs are consumed).
    monkeypatch.setattr(
        loop.agents, "run", _scripted_run(["claude:ready", "codex:revise"])
    )
    with pytest.raises(loop.Paused, match="visit cap"):
        planner._run_pipeline(repo, settings, DESCRIPTION, echo=False)


def test_a_crash_resume_advances_past_a_stage_whose_handoff_already_landed(
    repo, monkeypatch
):
    settings = settings_with_planner(repo)
    # Simulate a crash right after the draft stage wrote its "ready" handoff,
    # but before the loop processed it: write the handoff by hand, exactly as
    # fake_pipeline_agent.py itself would have produced it for that turn.
    handoff_path = repo / ".whyline" / "active-handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(
        json.dumps(
            {
                "id": "event1",
                "counter": 1,
                "task": planner.PLAN_TASK_ID,
                "to_actor": "claude",
                "status": "ready",
                "summary": "drafted",
                "from_actor": "",
            }
        )
    )
    # Only one more spec is provided: if consult_handoff mistakenly re-ran
    # draft instead of advancing to review, this would raise IndexError
    # (fake agent's spec list exhausted) instead of reaching "@complete".
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["claude:approved"]))
    planner._run_pipeline(
        repo,
        settings,
        DESCRIPTION,
        current_stage_id="draft",
        round_=1,
        stage_visits={"draft": 1},
        consult_handoff=True,
        echo=False,
    )
    assert state.load_plan(repo).stage == "@complete"
    logs = list((repo / ".whyline" / "relay" / "logs").glob("*"))
    assert any("review" in p.name for p in logs)
    assert not any("draft" in p.name for p in logs)


def test_a_human_driven_redraft_ignores_the_stale_approved_handoff(repo, monkeypatch):
    # After a completed session, the on-disk handoff still says "approved". A
    # fresh call with consult_handoff=False (the default) must not re-decide
    # against it -- it always starts a genuinely new turn at current_stage_id.
    settings = settings_with_planner(repo)
    monkeypatch.setattr(
        loop.agents, "run", _scripted_run(["claude:ready", "claude:approved"])
    )
    planner._run_pipeline(repo, settings, DESCRIPTION, echo=False)
    assert state.load_plan(repo).stage == "@complete"
    monkeypatch.setattr(
        loop.agents, "run", _scripted_run(["claude:ready", "claude:approved"])
    )
    planner._run_pipeline(
        repo,
        settings,
        DESCRIPTION,
        current_stage_id="draft",
        round_=1,
        stage_visits={"draft": 1},
        feedback="make it shorter",
        echo=False,
    )
    assert state.load_plan(repo).stage == "@complete"


def test_start_refuses_when_a_session_is_already_checkpointed(repo, monkeypatch):
    settings = settings_with_planner(repo)
    state.save_plan(
        repo,
        state.PlanState(
            description=DESCRIPTION,
            stage="draft",
            round=1,
            stage_visits={"draft": 1},
            agent="",
            feedback="",
            draft_path=str(planner.draft_path(repo)),
            paused_reason="",
            log_path="",
        ),
    )
    with pytest.raises(planner.PlanAlreadyInProgress):
        planner.start(repo, settings, DESCRIPTION)


def test_approving_writes_and_commits_plan_md_without_starting(repo, monkeypatch):
    settings = settings_with_planner(repo)
    # Built once, outside the wrapper: _scripted_run's list is consumed with
    # .pop(0), so it must be the *same* object across both turns. Building a
    # fresh two-element list inside `run` on every call (a first draft of this
    # test did exactly that) makes every turn pop "claude:ready" again, so the
    # second (review) turn wrongly receives "ready" instead of "approved" --
    # caught empirically as "unrecognised outcome 'ready' for stage 'review'".
    scripted = _scripted_run(["claude:ready", "claude:approved"])

    def run(
        command,
        prompt,
        *,
        cwd,
        log_path,
        timeout_seconds,
        which=None,
        echo=True,
        agent_name=None,
    ):
        if agent_name == "codex":
            planner.draft_path(repo).write_text("- [ ] T-1: build it\n  Do the thing.\n")
        return scripted(
            command,
            prompt,
            cwd=cwd,
            log_path=log_path,
            timeout_seconds=timeout_seconds,
            which=which,
            echo=echo,
            agent_name=agent_name,
        )

    monkeypatch.setattr(loop.agents, "run", run)
    result = planner.start(repo, settings, DESCRIPTION, confirm=_confirm(["a", "n"]))
    assert "Wrote" in result
    assert (repo / "plan.md").read_text() == "- [ ] T-1: build it\n  Do the thing.\n"
    assert state.load_plan(repo) is None
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout
    assert "plan" in log.lower()


def test_discarding_clears_the_checkpoint_and_keeps_the_draft_file(repo, monkeypatch):
    settings = settings_with_planner(repo)
    scripted = _scripted_run(["claude:ready", "claude:approved"])

    def run(
        command,
        prompt,
        *,
        cwd,
        log_path,
        timeout_seconds,
        which=None,
        echo=True,
        agent_name=None,
    ):
        if agent_name == "codex":
            planner.draft_path(repo).write_text("- [ ] T-1: x\n  y.\n")
        return scripted(
            command,
            prompt,
            cwd=cwd,
            log_path=log_path,
            timeout_seconds=timeout_seconds,
            which=which,
            echo=echo,
            agent_name=agent_name,
        )

    monkeypatch.setattr(loop.agents, "run", run)
    result = planner.start(repo, settings, DESCRIPTION, confirm=_confirm(["d"]))
    assert "Discarded" in result
    assert state.load_plan(repo) is None
    assert planner.draft_path(repo).exists()
    assert not (repo / "plan.md").exists()


def test_requesting_changes_redrafts_before_the_gate_reappears(repo, monkeypatch):
    settings = settings_with_planner(repo)
    calls = {"n": 0}

    def run(
        command,
        prompt,
        *,
        cwd,
        log_path,
        timeout_seconds,
        which=None,
        echo=True,
        agent_name=None,
    ):
        calls["n"] += 1
        spec = [
            "claude:ready",
            "claude:approved",
            "claude:ready",
            "claude:approved",
        ][calls["n"] - 1]
        if agent_name == "codex":
            planner.draft_path(repo).write_text(
                f"- [ ] T-1: round {calls['n']}\n  y.\n"
            )
        return _scripted_run([spec])(
            command,
            prompt,
            cwd=cwd,
            log_path=log_path,
            timeout_seconds=timeout_seconds,
            which=which,
            echo=echo,
            agent_name=agent_name,
        )

    monkeypatch.setattr(loop.agents, "run", run)
    result = planner.start(
        repo,
        settings,
        DESCRIPTION,
        confirm=_confirm(["r", "make it shorter", "a", "n"]),
    )
    assert "Wrote" in result
    # calls["n"] only advances the draft file's content on the draft (codex)
    # turn: 1 (session 1's draft) writes "round 1", 2 is session 1's review
    # turn (no write), 3 is session 2's draft turn after "request changes"
    # (writes "round 3"), 4 is session 2's review turn. The approved content
    # is whatever the draft stage wrote last -- "round 3" -- proving a real
    # second draft round happened, not a repeat of the first.
    assert "round 3" in (repo / "plan.md").read_text()
    assert "round 1" not in (repo / "plan.md").read_text()


def test_discard_with_no_session_says_so(repo):
    assert planner.discard(repo) == "Nothing to discard."


def test_resuming_at_complete_reprints_the_draft_without_rerunning_any_agent(
    repo, monkeypatch
):
    settings = settings_with_planner(repo)
    planner.draft_path(repo).parent.mkdir(parents=True, exist_ok=True)
    planner.draft_path(repo).write_text("- [ ] T-1: x\n  y.\n")
    saved = state.PlanState(
        description=DESCRIPTION,
        stage="@complete",
        round=2,
        stage_visits={"draft": 1, "review": 1},
        agent="",
        feedback="",
        draft_path=str(planner.draft_path(repo)),
        paused_reason="",
        log_path="",
    )
    state.save_plan(repo, saved)

    def run(*a, **k):
        raise AssertionError("no agent should run when resuming at @complete")

    monkeypatch.setattr(loop.agents, "run", run)
    result = planner.resume(repo, settings, saved, confirm=_confirm(["d"]))
    assert "Discarded" in result
