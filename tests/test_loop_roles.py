import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from whyline_relay import config, loop, plan


FAKE = str(Path(__file__).parent / "fake_role_agent.py")
TASK = plan.Task(task_id="T-1", text="T-1: Work", checked=False, line_index=0)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "relay/plan")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-m", "initial")
    (tmp_path / ".whyline").mkdir()
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda *args, **kwargs: "PACKET")
    return tmp_path


def command(
    root: Path, from_actor: str, to_actor: str, status: str, commit: str
) -> list[str]:
    return [
        sys.executable,
        FAKE,
        str(root),
        from_actor,
        to_actor,
        status,
        commit,
    ]


def settings(
    root: Path,
    roles: config.Roles = config.Roles("claude", "codex"),
) -> config.Config:
    base = config.load(root)
    return replace(
        base,
        roles=roles,
        agents={
            "claude": command(root, "claude", "codex", "ready-for-review", "no"),
            "codex": command(root, "codex", "codex", "approved", "yes"),
        },
    )


def test_swapped_roles_complete_and_claim_for_the_implementer(repo: Path, monkeypatch):
    claims = []
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *args: claims.append(args))
    outcome = loop.run_task(
        repo,
        settings(repo),
        TASK,
        base_commit=loop.gitcheck.head_commit(repo),
        echo=False,
    )
    assert outcome.committed is True
    assert claims == [(repo, "T-1", "claude", "implementer")]


def test_commit_check_follows_the_implementer(repo: Path, monkeypatch):
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *args: None)
    configured = settings(repo)
    configured = replace(
        configured,
        agents={
            **configured.agents,
            "claude": command(
                repo, "claude", "codex", "ready-for-review", "yes"
            ),
        },
    )
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(
            repo,
            configured,
            TASK,
            base_commit=loop.gitcheck.head_commit(repo),
            echo=False,
        )
    assert "claude made a commit, which the relay forbids" in raised.value.reason


def test_log_names_for_default_and_shared_agents(repo: Path, monkeypatch):
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *args: None)
    defaults = settings(repo, config.Roles())
    defaults = replace(
        defaults,
        agents={
            "codex": command(repo, "codex", "claude", "ready-for-review", "no"),
            "claude": command(repo, "claude", "claude", "approved", "yes"),
        },
    )
    loop.run_task(
        repo, defaults, TASK, base_commit=loop.gitcheck.head_commit(repo), echo=False
    )
    logs = repo / ".whyline" / "relay" / "logs"
    assert (logs / "T-1-1-codex.log").exists()
    assert (logs / "T-1-1-claude.log").exists()

    shared = replace(
        defaults,
        roles=config.Roles("claude", "claude"),
        agents={"claude": command(repo, "claude", "claude", "approved", "yes")},
    )
    real_run = loop.agents.run

    def run_for_role(command_, prompt, **kwargs):
        if "You are the implementer" in prompt:
            command_ = command(repo, "claude", "claude", "ready-for-review", "no")
        return real_run(command_, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", run_for_role)
    base = loop.gitcheck.head_commit(repo)
    loop.run_task(repo, shared, TASK, base_commit=base, echo=False)
    implementer_log = logs / "T-1-1-claude-implementer.log"
    reviewer_log = logs / "T-1-1-claude-reviewer.log"
    assert implementer_log.exists() and reviewer_log.exists()
    assert implementer_log.read_text() != reviewer_log.read_text()


def test_progress_verb_follows_the_role(repo: Path, monkeypatch, capsys):
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *args: None)
    loop.run_task(
        repo,
        settings(repo),
        TASK,
        base_commit=loop.gitcheck.head_commit(repo),
        echo=True,
    )
    output = capsys.readouterr().out
    assert "==> claude: implementing T-1 (round 1 of 3)" in output
    assert "==> codex: reviewing T-1" in output


def test_generic_agent_silence_uses_its_adapter(repo: Path, monkeypatch):
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *args: None)
    configured = settings(repo, config.Roles("codex", "aider"))
    configured = replace(
        configured,
        agents={
            "codex": command(repo, "codex", "aider", "ready-for-review", "no"),
            "aider": [sys.executable, "-c", "print('first'); print('second')"],
        },
        adapters={"aider": "generic"},
    )
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(
            repo,
            configured,
            TASK,
            base_commit=loop.gitcheck.head_commit(repo),
            echo=False,
        )
    assert "its last output was" in raised.value.reason
    assert "second" in raised.value.reason
