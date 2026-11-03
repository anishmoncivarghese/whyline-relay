"""A pause asked for during a turn waits for that turn's handoff, then stops
before the next agent; resume runs the next turn and never the finished one."""

import json
import re
import subprocess
from pathlib import Path

import pytest

from whyline_relay import cli, loop, plan, state

PIPELINE_CONFIG = (
    '[roles]\nimplementer = "codex"\ntester = "claude"\nreviewer = "claude"\n'
    '[pipeline]\ndefault_profile = "full"\n'
    '[pipeline.profiles]\nfull = ["draft", "test", "review"]\n'
    '[pipeline.stages.draft]\nrole = "implementer"\nprompt = "implement"\n'
    '[pipeline.stages.draft.on]\nready = "@next"\n'
    '[pipeline.stages.test]\nrole = "tester"\nprompt = "test"\nmax_visits = 1\n'
    '[pipeline.stages.test.on]\npassed = "@next"\nfailed = "draft"\n'
    '[pipeline.stages.review]\nrole = "reviewer"\nprompt = "review"\nmax_visits = 1\n'
    '[pipeline.stages.review.on]\napproved = "@complete"\nrejected = "draft"\n'
    '[agents.codex]\ncommand = ["codex"]\n'
    '[agents.claude]\ncommand = ["claude"]\n'
)
LEGACY_CONFIG = (
    '[roles]\nimplementer = "codex"\nreviewer = "claude"\n'
    '[agents.codex]\ncommand = ["codex"]\n'
    '[agents.claude]\ncommand = ["claude"]\n'
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(two_role_repo, config_text: str, plan_text: str) -> Path:
    root = two_role_repo(plan_text)
    (root / ".whyline/relay/config.toml").write_text(config_text)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "config")
    return root


def _fake_agent(monkeypatch, turns: list[dict]) -> list[str]:
    """Stand in for agents.run. Each turn is a dict with to/status and
    optionally `pause` (write PAUSE during the turn), `commit` (the legacy
    reviewer's own commit) or `limit` (no handoff, a usage-limit message).
    Returns the log name of every turn that ran, in order."""
    ran: list[str] = []

    def run(command, prompt, *, cwd, log_path, **kwargs):
        turn = turns.pop(0)
        root = Path(cwd)
        ran.append(log_path.stem)
        if turn.get("pause"):
            loop.request_pause(root)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if turn.get("limit"):
            log_path.write_text("You have exceeded your usage limit. Try again later.\n")
            return 1
        log_path.write_text("ok\n")
        task_id = re.search(r"^## Task (\S+)", prompt, re.M).group(1)
        if turn.get("commit"):
            (root / f"work-{task_id}.txt").write_text("x\n")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", f"feat: work ({task_id})")
        target = root / ".whyline" / "active-handoff.json"
        previous = json.loads(target.read_text()) if target.exists() else {}
        counter = int(previous.get("counter", 0)) + 1
        target.write_text(
            json.dumps(
                {
                    "id": f"event{counter}",
                    "counter": counter,
                    "task": task_id,
                    "to_actor": turn["to"],
                    "status": turn["status"],
                    "summary": f"{turn['status']} summary",
                    "from_actor": "",
                }
            )
        )
        return 0

    monkeypatch.setattr(loop.agents, "run", run)
    return ran


def _start(root: Path) -> int:
    return cli.main(["start", "--repo", str(root)])


def _resume(root: Path) -> int:
    return cli.main(["resume", "--repo", str(root)])


def _checked(root: Path, task_id: str) -> bool:
    return plan.find(plan.parse((root / "plan.md").read_text()), task_id).checked


def test_a_pipeline_pause_waits_for_the_handoff_and_resume_runs_the_next_stage(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo, PIPELINE_CONFIG, "- [ ] T-1: build\n")
    turns = [
        {"to": "claude", "status": "ready"},
        {"to": "claude", "status": "passed", "pause": True},
        {"to": "claude", "status": "approved"},
    ]
    ran = _fake_agent(monkeypatch, turns)

    assert _start(root) == cli.EXIT_PAUSED
    assert "Paused: paused by you" in capsys.readouterr().err
    # The test turn finished and handed off; review never started.
    assert ran == ["T-1-1-codex-draft", "T-1-2-claude-test"]
    record = json.loads((root / ".whyline/active-handoff.json").read_text())
    assert record["status"] == "passed"
    assert not loop.pause_path(root).exists()
    saved = state.load(root)
    assert saved.paused_reason == loop.PAUSED_BY_USER
    assert saved.paused_by == "user"
    assert saved.paused_at
    # The finished test turn's checkpoint, so resume re-decides its handoff.
    assert (saved.stage, saved.round, saved.stage_visits) == (
        "test", 2, {"draft": 1, "test": 1}
    )

    # test and review each allow one visit: a rerun or a double charge pauses.
    assert _resume(root) == cli.EXIT_OK, capsys.readouterr().err
    assert ran == ["T-1-1-codex-draft", "T-1-2-claude-test", "T-1-3-claude-review"]
    assert _checked(root, "T-1")


def test_a_pipeline_send_back_pause_hands_the_feedback_to_the_next_draft(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo, PIPELINE_CONFIG, "- [ ] T-1: build\n")
    prompts: list[str] = []
    turns = [
        {"to": "claude", "status": "ready"},
        {"to": "codex", "status": "failed", "pause": True},
        {"to": "claude", "status": "ready"},
    ]
    ran = _fake_agent(monkeypatch, turns)
    real_run = loop.agents.run

    def recording(command, prompt, **kwargs):
        prompts.append(prompt)
        return real_run(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", recording)

    assert _start(root) == cli.EXIT_PAUSED
    capsys.readouterr()
    # The rerun of test would exceed its one visit and pause on the cap.
    assert _resume(root) == cli.EXIT_PAUSED
    assert "1-visit cap" in capsys.readouterr().err
    assert ran == ["T-1-1-codex-draft", "T-1-2-claude-test", "T-1-3-codex-draft"]
    assert "failed summary" in prompts[2]


def test_a_legacy_pause_between_implementer_and_reviewer(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo, LEGACY_CONFIG, "- [ ] T-1: build\n")
    turns = [
        {"to": "claude", "status": "ready-for-review", "pause": True},
        {"to": "claude", "status": "approved", "commit": True},
    ]
    ran = _fake_agent(monkeypatch, turns)

    assert _start(root) == cli.EXIT_PAUSED
    assert "Paused: paused by you" in capsys.readouterr().err
    assert ran == ["T-1-1-codex"]
    assert not loop.pause_path(root).exists()
    saved = state.load(root)
    assert (saved.paused_by, saved.round) == ("user", 1)

    assert _resume(root) == cli.EXIT_OK, capsys.readouterr().err
    assert ran == ["T-1-1-codex", "T-1-1-claude"]
    assert _checked(root, "T-1")


def test_a_legacy_send_back_pause_charges_the_round_once(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo, LEGACY_CONFIG, "- [ ] T-1: build\n")
    turns = [
        {"to": "claude", "status": "ready-for-review"},
        {"to": "codex", "status": "changes-requested", "pause": True},
        {"to": "claude", "status": "ready-for-review"},
        {"to": "claude", "status": "approved", "commit": True},
    ]
    ran = _fake_agent(monkeypatch, turns)

    assert _start(root) == cli.EXIT_PAUSED
    capsys.readouterr()
    assert ran == ["T-1-1-codex", "T-1-1-claude"]

    assert _resume(root) == cli.EXIT_OK, capsys.readouterr().err
    # Round 2 for the next implementer turn: charged once, not zero or twice.
    assert ran == ["T-1-1-codex", "T-1-1-claude", "T-1-2-codex", "T-1-2-claude"]


def test_a_pause_between_tasks_starts_the_next_task_on_resume(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo, LEGACY_CONFIG, "- [ ] T-1: one\n- [ ] T-2: two\n")
    turns = [
        {"to": "claude", "status": "ready-for-review"},
        {"to": "claude", "status": "approved", "commit": True, "pause": True},
        {"to": "claude", "status": "ready-for-review"},
        {"to": "claude", "status": "approved", "commit": True},
    ]
    ran = _fake_agent(monkeypatch, turns)

    assert _start(root) == cli.EXIT_PAUSED
    assert "Paused: paused by you" in capsys.readouterr().err
    assert ran == ["T-1-1-codex", "T-1-1-claude"]
    assert _checked(root, "T-1") and not _checked(root, "T-2")
    saved = state.load(root)
    assert (saved.task_id, saved.base_commit, saved.paused_by) == ("T-2", "", "user")
    assert not loop.pause_path(root).exists()

    assert _resume(root) == cli.EXIT_OK, capsys.readouterr().err
    assert ran == ["T-1-1-codex", "T-1-1-claude", "T-2-1-codex", "T-2-1-claude"]
    assert _checked(root, "T-2")


def test_a_pause_asked_during_the_last_task_ends_with_the_plan(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo, LEGACY_CONFIG, "- [ ] T-1: one\n")
    turns = [
        {"to": "claude", "status": "ready-for-review"},
        {"to": "claude", "status": "approved", "commit": True, "pause": True},
    ]
    _fake_agent(monkeypatch, turns)

    assert _start(root) == cli.EXIT_OK, capsys.readouterr().err
    assert not loop.pause_path(root).exists()
    assert state.load(root) is None


def test_a_limit_pause_clears_the_request(two_role_repo, monkeypatch, capsys):
    root = _repo(two_role_repo, LEGACY_CONFIG, "- [ ] T-1: build\n")
    _fake_agent(monkeypatch, [{"limit": True, "pause": True}])

    assert _start(root) == cli.EXIT_PAUSED
    err = capsys.readouterr().err
    assert "paused by you" not in err
    assert not loop.pause_path(root).exists()
    saved = state.load(root)
    assert saved.paused_by == ""
    assert "limit" in saved.paused_reason.lower()


def test_resume_clears_a_leftover_request(two_role_repo, monkeypatch, capsys):
    root = _repo(two_role_repo, LEGACY_CONFIG, "- [ ] T-1: build\n")
    turns = [
        {"to": "claude", "status": "ready-for-review", "pause": True},
        {"to": "claude", "status": "approved", "commit": True},
    ]
    ran = _fake_agent(monkeypatch, turns)
    assert _start(root) == cli.EXIT_PAUSED
    capsys.readouterr()
    loop.request_pause(root)

    assert _resume(root) == cli.EXIT_OK, capsys.readouterr().err
    assert ran == ["T-1-1-codex", "T-1-1-claude"]
    assert not loop.pause_path(root).exists()


def test_old_state_files_still_load(tmp_path: Path):
    path = state.path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "plan": "plan.md",
                "branch": "relay/plan",
                "task_id": "T-1",
                "round": 2,
                "base_commit": "abc",
                "paused_reason": "crash",
                "log_path": "",
            }
        )
    )
    saved = state.load(tmp_path)
    assert (saved.paused_by, saved.paused_at) == ("", "")
