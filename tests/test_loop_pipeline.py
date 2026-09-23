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
