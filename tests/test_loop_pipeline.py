import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from whyline_relay import config, loop, plan, pipeline as pipeline_module, state

FAKE = str(Path(__file__).parent / "fake_pipeline_agent.py")


def make_pipeline(max_visits=3):
    return pipeline_module.Pipeline(
        roles={
            "implementer": pipeline_module.Role("implementer", agent="codex"),
            "tester": pipeline_module.Role("tester", agent="claude"),
            "reviewer": pipeline_module.Role("reviewer", agent="claude"),
        },
        stages={
            "draft": pipeline_module.Stage(
                "draft", "implementer", "implement", {"ready": "@next"}, max_visits
            ),
            "test": pipeline_module.Stage(
                "test",
                "tester",
                "test",
                {"passed": "@next", "failed": "draft"},
                max_visits,
            ),
            "review": pipeline_module.Stage(
                "review",
                "reviewer",
                "review",
                {"approved": "@complete", "rejected": "draft"},
                max_visits,
            ),
        },
        profiles={
            "full": pipeline_module.Profile("full", ("draft", "test", "review"))
        },
        default_profile="full",
    )


def settings_with_pipeline(
    root: Path, pipe: pipeline_module.Pipeline, fingerprint="fp1"
) -> config.Config:
    base = config.load(root)
    return config.Config(
        plan=base.plan,
        max_rounds=10,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={"codex": ["codex"], "claude": ["claude"]},
        status_map=base.status_map,
        pipeline=pipe,
        pipeline_fingerprint=fingerprint,
    )


TASK = plan.Task(task_id="T-1", text="T-1: build it", checked=False, line_index=0)


def test_run_agent_and_check_visit_cap_are_public():
    # Both were already shared internally by _run_task and _run_configured_task;
    # this plan adds a third caller (planner.py, Task 6) in a different module,
    # which needs them importable without a leading underscore.
    assert callable(loop.run_agent)
    assert callable(loop.check_visit_cap)


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
    prompt_dir = tmp_path / ".whyline" / "relay" / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "test.md").write_text(
        "{sync_packet}\n\n## Task {task_id}\n\n{task_text}\n"
    )
    monkeypatch.setattr(
        loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET"
    )
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    return tmp_path


def head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _scripted_run(specs: list[str]):
    """Stand in for agents.run: pops the next "[commit:]to_actor:status" spec and
    actually invokes fake_pipeline_agent.py with it, so the real handoff (and,
    for a "commit:"-prefixed spec, the real git commit) happens exactly as a
    genuine agent turn would produce it. Matches agents.run's real signature."""

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
        cancel_event=None,
        env=None,
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


def test_a_three_stage_pipeline_runs_end_to_end_with_a_bounce_back(repo, monkeypatch):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    # draft -> ready (tester); test -> failed (implementer); draft -> ready (tester);
    # test -> passed (reviewer); review -> approved (task complete)
    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted_run(
            [
                "claude:ready",
                "codex:failed",
                "claude:ready",
                "claude:passed",
                "claude:approved",
            ]
        ),
    )
    outcome = loop.run_task(repo, settings, TASK, base_commit=head(repo), echo=False)
    assert outcome.committed is True
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "(T-1)" in subject


def test_a_tester_stage_using_the_built_in_prompt_routes_correctly(repo, monkeypatch):
    (repo / ".whyline" / "relay" / "prompts" / "test.md").unlink()
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted_run(["claude:ready", "claude:passed", "claude:approved"]),
    )
    outcome = loop.run_task(repo, settings, TASK, base_commit=head(repo), echo=False)
    assert outcome.committed is True


def test_a_stage_hitting_its_visit_cap_pauses_instead_of_looping(repo, monkeypatch):
    pipe = make_pipeline(max_visits=1)
    settings = settings_with_pipeline(repo, pipe)
    monkeypatch.setattr(
        loop.agents, "run", _scripted_run(["claude:ready", "codex:failed"])
    )
    with pytest.raises(loop.Paused, match="1-visit cap"):
        loop.run_task(repo, settings, TASK, base_commit=head(repo), echo=False)


def test_a_non_terminal_stage_committing_is_refused(repo, monkeypatch):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["commit:claude:ready"]))
    with pytest.raises(loop.Paused, match="only the relay itself commits"):
        loop.run_task(repo, settings, TASK, base_commit=head(repo), echo=False)


def test_a_terminal_stage_committing_is_also_refused(repo, monkeypatch):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted_run(["claude:ready", "claude:passed", "commit:claude:approved"]),
    )
    with pytest.raises(loop.Paused, match="only the relay itself commits"):
        loop.run_task(repo, settings, TASK, base_commit=head(repo), echo=False)


def test_a_stage_that_committed_on_its_own_pauses_with_head_moved(repo, monkeypatch):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    base = head(repo)
    (repo / "feature.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: sneaky (T-1)"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    moved = head(repo)
    (repo / ".whyline" / "active-handoff.json").write_text(
        '{"id": "event3", "task": "T-1", "to_actor": "claude", "status": "approved", '
        '"summary": "feat: the whole feature", "from_actor": ""}'
    )
    state.save(
        repo,
        state.RelayState(
            plan="plan.md",
            branch="relay/T-1",
            task_id="T-1",
            round=3,
            base_commit=base,
            paused_reason="crash",
            log_path="",
            profile="full",
            stage="@complete",
            stage_visits={"draft": 1, "test": 1, "review": 1},
            pipeline_fingerprint=settings.pipeline_fingerprint,
        ),
    )
    called = []
    monkeypatch.setattr(loop.agents, "run", lambda *a, **k: called.append("ran"))

    with pytest.raises(loop.Paused, match="HEAD moved"):
        loop.run_task(
            repo, settings, TASK, base_commit=base, echo=False, resume=True
        )

    assert called == []
    assert head(repo) == moved


def test_the_relay_makes_the_commit_not_the_agent(repo, monkeypatch):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    # None of these specs run "commit:" -- if a real commit exists afterward,
    # the relay made it, not any agent turn.
    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted_run(["claude:ready", "claude:passed", "claude:approved"]),
    )
    outcome = loop.run_task(repo, settings, TASK, base_commit=head(repo), echo=False)
    assert outcome.committed is True
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "(T-1)" in subject


def test_crash_between_approval_and_commit_resumes_straight_to_the_commit(
    repo, monkeypatch
):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    # Simulate a crash right after the terminal "review" stage handed off "approved"
    # and the relay checkpointed stage="@complete", but before it made its own
    # commit: the handoff on disk already says approved; state.json says @complete.
    (repo / "feature.txt").write_text("x\n")
    (repo / ".whyline" / "active-handoff.json").write_text(
        '{"id": "event3", "task": "T-1", "to_actor": "claude", "status": "approved", '
        '"summary": "feat: the whole feature", "from_actor": ""}'
    )
    state.save(
        repo,
        state.RelayState(
            plan="plan.md",
            branch="relay/T-1",
            task_id="T-1",
            round=3,
            base_commit=head(repo),
            paused_reason="crash",
            log_path="",
            profile="full",
            stage="@complete",
            stage_visits={"draft": 1, "test": 1, "review": 1},
            pipeline_fingerprint=settings.pipeline_fingerprint,
        ),
    )
    called = []
    monkeypatch.setattr(loop.agents, "run", lambda *a, **k: called.append("ran"))
    outcome = loop.run_task(
        repo, settings, TASK, base_commit=head(repo), echo=False, resume=True
    )
    assert outcome.committed is True
    assert called == [], "no agent should run when only the commit itself was pending"
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "feat: the whole feature (T-1)" in subject


def test_crash_after_approval_before_complete_checkpoint_retries_commit(
    repo, monkeypatch
):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    # Simulate a crash after the terminal stage wrote its approved handoff but
    # before the relay checkpointed stage="@complete": state still names review.
    (repo / "feature.txt").write_text("x\n")
    (repo / ".whyline" / "active-handoff.json").write_text(
        '{"id": "event3", "task": "T-1", "to_actor": "claude", '
        '"status": "approved", "summary": "feat: the whole feature", '
        '"from_actor": ""}'
    )
    state.save(
        repo,
        state.RelayState(
            plan="plan.md",
            branch="relay/T-1",
            task_id="T-1",
            round=3,
            base_commit=head(repo),
            paused_reason="crash",
            log_path="",
            profile="full",
            stage="review",
            stage_visits={"draft": 1, "test": 1, "review": 1},
            pipeline_fingerprint=settings.pipeline_fingerprint,
        ),
    )
    called = []
    monkeypatch.setattr(loop.agents, "run", lambda *a, **k: called.append("ran"))
    outcome = loop.run_task(
        repo, settings, TASK, base_commit=head(repo), echo=False, resume=True
    )
    assert outcome.committed is True
    assert called == [], "an approved terminal stage must not be run again"
    subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "feat: the whole feature (T-1)" in subject


def test_resume_reconstructs_stage_and_advances_past_the_unrouted_handoff(
    repo, monkeypatch
):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    # Simulate a crash right after draft wrote its handoff but before it was routed:
    # state.json says we were about to run "draft" for its 1st visit, and the
    # handoff already on disk says draft finished with "ready".
    (repo / ".whyline" / "active-handoff.json").write_text(
        '{"id": "event1", "task": "T-1", "to_actor": "claude", "status": "ready", '
        '"summary": "x", "from_actor": ""}'
    )
    state.save(
        repo,
        state.RelayState(
            plan="plan.md",
            branch="relay/T-1",
            task_id="T-1",
            round=1,
            base_commit=head(repo),
            paused_reason="crash",
            log_path="",
            profile="full",
            stage="draft",
            stage_visits={"draft": 1},
            pipeline_fingerprint=settings.pipeline_fingerprint,
        ),
    )
    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted_run(["claude:passed", "claude:approved"]),
    )
    outcome = loop.run_task(
        repo, settings, TASK, base_commit=head(repo), echo=False, resume=True
    )
    assert outcome.committed is True


def _run_turns(turns: list[tuple[str, str, str]]):
    """Stand in for agents.run. Each turn is (to_actor, status, summary)."""

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
        cancel_event=None,
        env=None,
    ):
        to_actor, status, summary = turns.pop(0)
        root = Path(cwd)
        match = re.search(r"^## Task (\S+)", prompt, re.M)
        task_id = match.group(1) if match else "T-1"
        target = root / ".whyline" / "active-handoff.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = json.loads(target.read_text()) if target.exists() else {}
        counter = int(previous.get("counter") or 0) + 1
        target.write_text(
            json.dumps(
                {
                    "id": f"event{counter}",
                    "counter": counter,
                    "task": task_id,
                    "to_actor": to_actor,
                    "status": status,
                    "summary": summary,
                    "from_actor": "",
                    "questions": [],
                }
            )
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(summary)
        return 0

    return run


@pytest.mark.parametrize(
    "scripts, agent",
    [
        (["codex:blocked"], "codex"),
        (["claude:ready", "codex:blocked"], "claude"),
        (["claude:ready", "claude:passed", "codex:blocked"], "claude"),
    ],
)
def test_blocked_from_draft_test_and_review_pauses(repo, monkeypatch, scripts, agent):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(scripts))
    with pytest.raises(
        loop.Paused, match=f"{agent} reported blocked: fake codex:blocked"
    ):
        loop.run_task(repo, settings, TASK, base_commit=head(repo), echo=False)


def test_a_rejected_verification_denial_pauses_without_counting_a_draft_visit(
    repo, monkeypatch
):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    monkeypatch.setattr(
        loop.agents,
        "run",
        _run_turns(
            [
                ("claude", "ready", "implemented"),
                ("claude", "passed", "tests passed"),
                ("codex", "rejected", "verification was denied"),
            ]
        ),
    )
    seen: list[dict[str, int]] = []

    def on_turn(round_, previous_id, stage_state):
        seen.append(dict(stage_state["stage_visits"]))

    with pytest.raises(loop.Paused) as raised:
        loop.run_task(
            repo, settings, TASK, base_commit=head(repo), echo=False, on_turn=on_turn
        )
    assert (
        raised.value.reason
        == "claude could not run a required command: verification was denied"
    )
    assert seen[-1]["draft"] == 1
    assert all(visits.get("draft", 0) == 1 for visits in seen)


def test_a_rejected_defect_still_routes_to_draft(repo, monkeypatch):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    seen: list[tuple[str, dict[str, int]]] = []

    def on_turn(round_, previous_id, stage_state):
        seen.append((stage_state["stage"], dict(stage_state["stage_visits"])))

    monkeypatch.setattr(
        loop.agents,
        "run",
        _scripted_run(
            [
                "claude:ready",
                "claude:passed",
                "codex:rejected",
                "claude:ready",
                "claude:passed",
                "claude:approved",
            ]
        ),
    )
    outcome = loop.run_task(
        repo, settings, TASK, base_commit=head(repo), echo=False, on_turn=on_turn
    )
    assert outcome.committed is True
    assert ("draft", {"draft": 2, "test": 1, "review": 1}) in seen


def test_sandbox_markers_match_case_insensitively_and_only_on_an_earlier_stage():
    pipe = make_pipeline()
    for marker in loop.SANDBOX_MARKERS:
        assert loop._summary_reports_sandbox_refusal(f"prefix {marker.upper()} suffix")
        assert loop._sandbox_backroute(pipe, "full", "review", "draft", marker.upper())
    assert not loop._summary_reports_sandbox_refusal("the assertion on line 4 is wrong")
    assert not loop._sandbox_backroute(
        pipe, "full", "review", "draft", "the assertion on line 4 is wrong"
    )
    # A forward route that merely mentions a marker is still a route.
    assert not loop._sandbox_backroute(
        pipe, "full", "draft", "test", "permission denied"
    )
    assert loop._sandbox_backroute(
        pipe, "full", "test", "draft", "Operation Not Permitted"
    )


def test_resume_pauses_a_sandbox_rejection_without_starting_draft(repo, monkeypatch):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe)
    (repo / ".whyline" / "active-handoff.json").write_text(
        json.dumps(
            {
                "id": "event3",
                "task": "T-1",
                "to_actor": "codex",
                "status": "rejected",
                "summary": "verification was denied",
                "from_actor": "",
            }
        )
    )
    state.save(
        repo,
        state.RelayState(
            plan="plan.md",
            branch="relay/T-1",
            task_id="T-1",
            round=3,
            base_commit=head(repo),
            paused_reason="crash",
            log_path="",
            profile="full",
            stage="review",
            stage_visits={"draft": 1, "test": 1, "review": 1},
            pipeline_fingerprint=settings.pipeline_fingerprint,
        ),
    )
    called: list[str] = []
    monkeypatch.setattr(loop.agents, "run", lambda *a, **k: called.append("ran"))
    with pytest.raises(
        loop.Paused,
        match="claude could not run a required command: verification was denied",
    ):
        loop.run_task(
            repo, settings, TASK, base_commit=head(repo), echo=False, resume=True
        )
    assert called == []


def test_resume_with_a_drifted_fingerprint_pauses_before_running_anything(
    repo, monkeypatch
):
    pipe = make_pipeline()
    settings = settings_with_pipeline(repo, pipe, fingerprint="fp-new")
    state.save(
        repo,
        state.RelayState(
            plan="plan.md",
            branch="relay/T-1",
            task_id="T-1",
            round=1,
            base_commit=head(repo),
            paused_reason="crash",
            log_path="",
            profile="full",
            stage="draft",
            stage_visits={"draft": 1},
            pipeline_fingerprint="fp-old",
        ),
    )
    called: list[str] = []
    monkeypatch.setattr(loop.agents, "run", lambda *a, **k: called.append("ran"))
    with pytest.raises(loop.Paused, match="pipeline has changed"):
        loop.run_task(
            repo, settings, TASK, base_commit=head(repo), echo=False, resume=True
        )
    assert called == []


FAKE_ROLE = str(Path(__file__).parent / "fake_role_agent.py")
RATE_LIMITED = """#!/usr/bin/env python3
import sys
print("You have exceeded your usage limit.")
sys.exit(1)
"""


def test_a_pipeline_stage_switches_to_the_chain_backup_on_no_handoff(
    repo, tmp_path
):
    from whyline_relay import failover, pipeline as pipeline_module

    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    pipe = pipeline_module.Pipeline(
        roles={"solo": pipeline_module.Role(name="solo", agent="codex")},
        stages={
            "work": pipeline_module.Stage(
                id="work",
                role="solo",
                prompt="implement",
                transitions={"done": "@complete"},
            )
        },
        profiles={
            "default": pipeline_module.Profile(name="default", stages=("work",))
        },
        default_profile="default",
    )
    base = config.load(repo)
    settings = config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, str(limited)],
            "aider": [
                sys.executable,
                FAKE_ROLE,
                str(repo),
                "aider",
                "aider",
                "done",
                "no",
            ],
        },
        status_map=base.status_map,
        adapters={"aider": "generic"},
        backup_chain=["aider"],
        pipeline=pipe,
        pipeline_fingerprint="test",
    )
    base_commit = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base_commit, echo=False)
    assert outcome.committed
    overrides = failover.read_overrides(repo)
    assert overrides["solo"].agent == "aider"
    assert overrides["solo"].tried == ["codex"]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def _commit(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message], cwd=root, check=True, capture_output=True
    )


def _scenario(scenario_id: str, covers: str, *, kind: str = "auto", note: str = "") -> str:
    lines = [
        f"- [ ] {scenario_id}: {scenario_id} title",
        f"  covers: {covers}",
        f"  kind: {kind}",
        "  steps:",
        f"    - run {scenario_id}",
        "  expected:",
        f"    - {scenario_id} holds",
    ]
    if note:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


_PHASE_PLAN = """\
- [x] EARLY: before the release
  Already shipped.
- [x] REL-0: previous release
  relay-profile: release
- [x] MID-1: in range
  Do the first half.
- [x] MID-2: also in range
  Do the second half.
- [ ] VER-1: verify the phase
  relay-profile: verify
- [ ] GAP: after the verify task
  Not part of this verify.
- [ ] REL-1: next release
  relay-profile: release
- [ ] LATE: after the release
  Belongs to the next phase.
"""


def _phase_tests() -> str:
    parts = [
        _scenario("TP-EARLY", "EARLY"),
        _scenario("TP-MID", "MID-1", note="keep"),
        _scenario("TP-SCREEN", "MID-1", kind="manual"),
        _scenario("TP-MID2", "MID-1, MID-2"),
        _scenario("TP-GAP", "GAP"),
        _scenario("TP-SPAN", "MID-2, LATE"),
        _scenario("TP-LATE", "LATE"),
    ]
    return "\n".join(parts) + "\n"


def _listed(prompt: str) -> list[str]:
    return re.findall(r"(?m)^### (\S+):", prompt)


def _capturing_run(specs: list[str], captured: list[str], *, edit: Path | None = None):
    base = _scripted_run(specs)
    edited = {"done": False}

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
        cancel_event=None,
        env=None,
    ):
        captured.append(prompt)
        if edit is not None and not edited["done"]:
            edit.write_text(edit.read_text().replace("note: keep", "note: needs a device"))
            edited["done"] = True
        return base(
            command,
            prompt,
            cwd=cwd,
            log_path=log_path,
            timeout_seconds=timeout_seconds,
            which=which,
            echo=echo,
            agent_name=agent_name,
            cancel_event=cancel_event,
            env=env,
        )

    return run


def _task_commit(root: Path, task_id: str) -> str:
    needle = f"({task_id})"
    for line in _git(root, "log", "--pretty=format:%H %s").splitlines():
        sha, subject = line.split(" ", 1)
        if needle in subject and not subject.startswith("chore: tick"):
            return sha
    raise AssertionError(_git(root, "log", "--pretty=format:%s"))


def test_verify_prompt_lists_exactly_the_in_range_auto_scenarios(repo, monkeypatch):
    (repo / "plan.md").write_text(_PHASE_PLAN)
    tests = repo / "plan.tests.md"
    tests.write_text(_phase_tests())
    _commit(repo, "plan")
    captured: list[str] = []
    monkeypatch.setattr(
        loop.agents,
        "run",
        _capturing_run(
            ["claude:ready", "claude:passed", "claude:approved"],
            captured,
            edit=tests,
        ),
    )
    outcomes = loop.run_plan(
        repo,
        settings_with_pipeline(repo, make_pipeline()),
        repo / "plan.md",
        branch="main",
        only="VER-1",
        echo=False,
    )
    assert [outcome.task_id for outcome in outcomes] == ["VER-1"]
    assert len(captured) == 3
    for prompt in captured:
        assert _listed(prompt) == ["TP-MID", "TP-MID2", "TP-SPAN"]
        assert "TP-EARLY" not in prompt
        assert "TP-SCREEN" not in prompt
        assert "TP-GAP" not in prompt
        assert "TP-LATE" not in prompt
        assert "tests/acceptance/" in prompt
        assert "named after its scenario id" in prompt
        assert "ready only when those tests pass" in prompt
        assert "kind: manual" in prompt
        assert "not skipped" in prompt
        assert "profile: full" in prompt
    sha = _task_commit(repo, "VER-1")
    assert "plan.tests.md" in _git(repo, "show", "--name-only", "--pretty=format:", sha).split()
    assert "needs a device" in _git(repo, "show", f"{sha}:plan.tests.md")
    assert _git(repo, "status", "--porcelain") == ""


def test_verify_without_a_test_plan_runs_as_an_ordinary_task(repo, monkeypatch):
    (repo / "plan.md").write_text(
        "- [ ] VER-1: verify the phase\n"
        "  relay-profile: verify\n"
        "  Check it.\n"
        "- [ ] PLAIN: an ordinary task\n"
        "  Do it.\n"
    )
    _commit(repo, "plan")
    captured: list[str] = []
    monkeypatch.setattr(
        loop.agents,
        "run",
        _capturing_run(
            ["claude:ready", "claude:passed", "claude:approved"],
            captured,
        ),
    )
    outcomes = loop.run_plan(
        repo,
        settings_with_pipeline(repo, make_pipeline()),
        repo / "plan.md",
        branch="main",
        only="VER-1",
        echo=False,
    )
    assert [outcome.task_id for outcome in outcomes] == ["VER-1"]
    assert len(captured) == 3
    for prompt in captured:
        assert "Automatic scenarios" not in prompt
        assert "tests/acceptance/" not in prompt
        assert "kind: manual" not in prompt
        assert "profile: full" in prompt
    assert "- [x] VER-1" in (repo / "plan.md").read_text()
    assert "plan.tests.md" not in _git(repo, "log", "--name-only", "--pretty=format:")


def _commit_one_file_then(path: Path, message: str, specs: list[str], contents: str):
    """First agent turn commits exactly `path`, then the scripted handoffs run."""
    scripted = _scripted_run(specs)
    committed = {"done": False}

    def run(command, prompt, **kwargs):
        if not committed["done"]:
            committed["done"] = True
            cwd = kwargs["cwd"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
            relative = path.resolve().relative_to(Path(cwd).resolve())
            subprocess.run(
                ["git", "add", "--", str(relative)],
                cwd=cwd,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=cwd,
                check=True,
                capture_output=True,
            )
        return scripted(command, prompt, **kwargs)

    return run


def test_a_config_only_commit_during_a_task_moves_the_base(repo, monkeypatch, capsys):
    config_path = repo / ".whyline" / "relay" / "config.toml"
    monkeypatch.setattr(
        loop.agents,
        "run",
        _commit_one_file_then(
            config_path,
            "setup: backup chain grok",
            ["claude:ready", "claude:passed", "claude:approved"],
            'plan = "plan.md"\n',
        ),
    )
    outcome = loop.run_task(
        repo,
        settings_with_pipeline(repo, make_pipeline()),
        TASK,
        base_commit=head(repo),
        echo=True,
    )
    assert outcome.committed is True
    settings_sha = next(
        line.split(" ", 1)[0]
        for line in _git(repo, "log", "--pretty=format:%H %s").splitlines()
        if "setup: backup chain grok" in line
    )
    task_sha = _task_commit(repo, "T-1")
    assert _git(repo, "rev-parse", "--verify", f"{task_sha}^").strip() == settings_sha
    short = settings_sha[:7]
    assert (
        f"Settings changed during T-1 ({short} setup: backup chain grok); "
        f"continuing from {short}."
        in capsys.readouterr().out
    )
    names = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", settings_sha)
    assert names.split() == [".whyline/relay/config.toml"]


def test_a_commit_touching_any_other_file_still_pauses(repo, monkeypatch):
    monkeypatch.setattr(
        loop.agents,
        "run",
        _commit_one_file_then(
            repo / "README.md",
            "feat: not settings",
            ["claude:ready"],
            "changed\n",
        ),
    )
    with pytest.raises(loop.Paused, match="only the relay itself commits"):
        loop.run_task(
            repo,
            settings_with_pipeline(repo, make_pipeline()),
            TASK,
            base_commit=head(repo),
            echo=False,
        )


def test_overload_uses_the_backup_for_one_turn_then_the_configured_agent(
    repo, monkeypatch, capsys
):
    from whyline_relay import failover

    specs = [
        "claude:ready",
        "claude:passed",
        "codex:rejected",
        "claude:ready",
        "claude:passed",
        "claude:approved",
    ]
    scripted = _scripted_run(specs)
    seen: list[str] = []
    overloaded = {"done": False}

    def run(command, prompt, **kwargs):
        agent_name = kwargs.get("agent_name")
        seen.append(agent_name)
        if agent_name == "codex" and not overloaded["done"]:
            overloaded["done"] = True
            log_path = kwargs["log_path"]
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("Selected model is at capacity\n")
            return 1
        return scripted(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", run)
    settings = dataclasses.replace(
        settings_with_pipeline(repo, make_pipeline()),
        agents={"codex": ["codex"], "claude": ["claude"], "grok": ["grok"]},
        adapters={"grok": "generic"},
        backup_chain=["grok"],
    )
    outcome = loop.run_task(repo, settings, TASK, base_commit=head(repo), echo=True)
    assert outcome.committed is True
    assert seen == ["codex", "grok", "claude", "claude", "codex", "claude", "claude"]
    assert failover.read_overrides(repo) == {}
    assert not (repo / ".whyline" / "relay" / "active-roles.json").exists()
    assert failover.pipeline_effective_agent(repo, settings, "implementer") == "codex"
    assert (
        "codex: model at capacity; using grok for this turn"
        in capsys.readouterr().out
    )
