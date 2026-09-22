import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from whyline_relay import config, failover, loop, plan


FAKE = str(Path(__file__).parent / "fake_role_agent.py")
TASK = plan.Task(task_id="T-1", text="T-1: Work", checked=False, line_index=0)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True
        )

    git("init", "-b", "relay/plan")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "README.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-m", "initial")
    (tmp_path / ".whyline").mkdir()
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda *a, **k: "PACKET")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    return tmp_path


RATE_LIMITED = '''#!/usr/bin/env python3
import sys
print("You have exceeded your usage limit.")
sys.exit(1)
'''


def settings_with_backup(
    root, implementer_command, backup_agent, backup_command, backups
):
    base = config.load(root)
    return config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": implementer_command,
            "claude": [
                sys.executable,
                FAKE,
                str(root),
                "claude",
                "claude",
                "approved",
                "yes",
            ],
            backup_agent: backup_command,
        },
        status_map=base.status_map,
        adapters={backup_agent: "generic"},
        backups=backups,
    )


def test_a_rate_limited_implementer_switches_to_its_backup_and_the_task_completes(
    repo, tmp_path
):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = settings_with_backup(
        repo,
        [sys.executable, str(limited)],
        "aider",
        [
            sys.executable,
            FAKE,
            str(repo),
            "aider",
            "claude",
            "ready-for-review",
            "no",
        ],
        {"implementer": "aider"},
    )
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert outcome.committed
    assert failover.read_overrides(repo)["implementer"].agent == "aider"
    assert failover.read_overrides(repo)["implementer"].reason == "rate-limit"


def test_the_rendered_prompt_names_the_switched_agent_not_the_static_config(
    repo, tmp_path, monkeypatch
):
    """Regression: _run_agent must render {implementer}/{reviewer} from the

    resolved effective agent, not settings.roles directly — caught by an
    acceptance test whose stand-in reads the rendered prompt (as a real agent
    would) rather than being told its from/to actor on the command line.
    """
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = settings_with_backup(
        repo,
        [sys.executable, str(limited)],
        "aider",
        [sys.executable, FAKE, str(repo), "aider", "claude", "ready-for-review", "no"],
        {"implementer": "aider"},
    )
    seen = []
    real_render = loop.prompts.render

    def spying_render(template, **kwargs):
        seen.append(kwargs)
        return real_render(template, **kwargs)

    monkeypatch.setattr(loop.prompts, "render", spying_render)
    base = loop.gitcheck.head_commit(repo)
    loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    implementer_turns = [k for k in seen if k["implementer"] != "codex" or True]
    # The turn that actually ran after the switch must have been told "aider", not "codex".
    assert any(k["implementer"] == "aider" for k in seen), seen


def test_an_unauthenticated_implementer_switches_to_its_backup(
    repo, tmp_path, monkeypatch
):
    # Exercising a real "codex login status" failure would need a binary literally named
    # "codex" on PATH (failover_reason's guard, FBO-2, refuses to check anything else) —
    # so this tests loop.py's handling of an "auth" verdict directly, the same way FBO-2's own
    # tests exercise failover_reason's detection logic directly. The real detection is FBO-2's job.
    silent = tmp_path / "silent.py"
    silent.write_text("import sys\nsys.exit(0)\n")
    settings = settings_with_backup(
        repo,
        [sys.executable, str(silent)],
        "aider",
        [
            sys.executable,
            FAKE,
            str(repo),
            "aider",
            "claude",
            "ready-for-review",
            "no",
        ],
        {"implementer": "aider"},
    )
    monkeypatch.setattr(
        loop.failover,
        "failover_reason",
        lambda adapter, text, command, runner=None: "auth",
    )
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert outcome.committed
    assert failover.read_overrides(repo)["implementer"].reason == "auth"


def test_a_switch_does_not_consume_a_review_round(repo, tmp_path):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = settings_with_backup(
        repo,
        [sys.executable, str(limited)],
        "aider",
        [
            sys.executable,
            FAKE,
            str(repo),
            "aider",
            "claude",
            "ready-for-review",
            "no",
        ],
        {"implementer": "aider"},
    )
    settings = replace(settings, max_rounds=1)
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert outcome.committed and outcome.rounds == 1


def test_no_backup_configured_pauses_exactly_as_0_2_3(repo, tmp_path):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    base_settings = config.load(repo)
    settings = replace(
        base_settings,
        agents={
            **base_settings.agents,
            "codex": [sys.executable, str(limited)],
        },
    )
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert (
        raised.value.reason
        == "codex hit a usage or rate limit; try again when it resets"
    )


def test_the_backup_also_failing_pauses_and_names_both(repo, tmp_path):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = settings_with_backup(
        repo,
        [sys.executable, str(limited)],
        "aider",
        [sys.executable, str(limited)],
        {"implementer": "aider"},
    )
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert "backup for implementer" in raised.value.reason
    assert "codex was already out" in raised.value.reason


def test_dry_run_shows_the_effective_backup_agent(tmp_path, monkeypatch, capsys):
    from whyline_relay import cli

    (tmp_path / ".whyline").mkdir()
    (tmp_path / "plan.md").write_text("- [ ] T-1: x\n  y\n")
    monkeypatch.setattr(
        cli.whylinecmd, "sync", lambda root, task, runner=None: "PACKET"
    )
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir()
    (relay / "config.toml").write_text(
        '[roles.backup]\nimplementer = "claude"\n'
    )
    failover.write_override(
        tmp_path,
        "implementer",
        failover.ActiveOverride("claude", "codex", "rate-limit", "t"),
    )
    code = cli.main(["start", "--repo", str(tmp_path), "--dry-run"])
    assert code == 0
    assert (
        "claude"
        in capsys.readouterr().out.split("Would run:")[1].splitlines()[0]
    )
