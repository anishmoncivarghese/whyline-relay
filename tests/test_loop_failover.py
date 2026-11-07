import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from whyline_relay import config, failover, loop, plan, roles


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
    root, implementer_command, backup_agent, backup_command, chain: list[str]
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
        backup_chain=chain,
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
        ["aider"],
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
        ["aider"],
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
        ["aider"],
    )
    monkeypatch.setattr(
        loop.failover,
        "classify",
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
        ["aider"],
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
        '[backup]\nchain = ["claude"]\n'
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


def test_a_chain_of_two_backups_walks_past_the_first_when_it_also_fails(
    repo, tmp_path
):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    also_limited = tmp_path / "also_limited.py"
    also_limited.write_text(RATE_LIMITED)
    base = config.load(repo)
    settings = config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, str(limited)],
            "claude": [
                sys.executable, FAKE, str(repo), "claude", "claude",
                "approved", "yes",
            ],
            "aider": [sys.executable, str(also_limited)],
            "cline": [
                sys.executable, FAKE, str(repo), "cline", "claude",
                "ready-for-review", "no",
            ],
        },
        status_map=base.status_map,
        adapters={"aider": "generic", "cline": "generic"},
        backup_chain=["aider", "cline"],
    )
    base_commit = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base_commit, echo=False)
    assert outcome.committed
    overrides = failover.read_overrides(repo)
    assert overrides["implementer"].agent == "cline"
    assert overrides["implementer"].tried == ["codex", "aider"]


def test_chain_exhaustion_pauses_with_a_distinct_message(repo, tmp_path):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    also_limited = tmp_path / "also_limited.py"
    also_limited.write_text(RATE_LIMITED)
    base = config.load(repo)
    settings = config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, str(limited)],
            "claude": [
                sys.executable, FAKE, str(repo), "claude", "claude",
                "approved", "yes",
            ],
            "aider": [sys.executable, str(also_limited)],
        },
        status_map=base.status_map,
        adapters={"aider": "generic"},
        backup_chain=["aider"],
    )
    base_commit = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as excinfo:
        loop.run_task(repo, settings, TASK, base_commit=base_commit, echo=False)
    assert "every backup in the chain is unavailable" in str(excinfo.value)
    assert "codex" in str(excinfo.value)
    assert "aider" in str(excinfo.value)


QUOTA = '''#!/usr/bin/env python3
import sys
print("RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 158h14m22s.")
sys.exit(1)
'''

OVERLOAD = '''#!/usr/bin/env python3
import sys
print("Selected model is at capacity")
sys.exit(1)
'''


def test_antigravity_quota_switches_to_the_first_backup_and_stores_the_reset(
    repo, tmp_path
):
    from datetime import datetime, timedelta

    limited = tmp_path / "quota.py"
    limited.write_text(QUOTA)
    settings = settings_with_backup(
        repo,
        [sys.executable, str(limited)],
        "aider",
        [
            sys.executable, FAKE, str(repo), "aider", "claude",
            "ready-for-review", "no",
        ],
        ["aider"],
    )
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert outcome.committed
    override = failover.read_overrides(repo)["implementer"]
    assert override.agent == "aider"
    assert override.reason == "rate-limit"
    reset = datetime.fromisoformat(override.resets_at)
    remaining = reset - datetime.now(reset.tzinfo)
    assert timedelta(hours=158, minutes=10) < remaining < timedelta(hours=158, minutes=20)


def test_overload_hands_the_turn_to_the_backup_without_a_sticky_override(
    repo, tmp_path, monkeypatch, capsys
):
    overloaded = tmp_path / "over.py"
    overloaded.write_text(OVERLOAD)
    seen = []
    real_run = loop.agents.run

    def spy(command, prompt, **kwargs):
        seen.append(kwargs.get("agent_name"))
        return real_run(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", spy)
    settings = settings_with_backup(
        repo,
        [sys.executable, str(overloaded)],
        "aider",
        [
            sys.executable, FAKE, str(repo), "aider", "claude",
            "ready-for-review", "no",
        ],
        ["aider"],
    )
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=True)
    assert outcome.committed
    # codex overloads, aider takes that turn, and the review stays on claude.
    assert seen[:3] == ["codex", "aider", "claude"]
    assert failover.read_overrides(repo) == {}
    assert (
        "codex: model at capacity; using aider for this turn"
        in capsys.readouterr().out
    )


def test_overload_with_an_exhausted_chain_pauses_for_capacity(repo, tmp_path):
    overloaded = tmp_path / "over.py"
    overloaded.write_text(OVERLOAD)
    settings = settings_with_backup(
        repo,
        [sys.executable, str(overloaded)],
        "aider",
        [sys.executable, str(overloaded)],
        ["aider"],
    )
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    reason = raised.value.reason
    assert reason == (
        "aider hit a temporary capacity error (Selected model is at capacity); "
        "resume in a few minutes or change its model"
    )
    assert "every backup in the chain is unavailable" not in reason
    assert failover.read_overrides(repo) == {}


def test_overload_with_no_backup_pauses_for_capacity(repo, tmp_path):
    overloaded = tmp_path / "over.py"
    overloaded.write_text(OVERLOAD)
    base_settings = config.load(repo)
    settings = replace(
        base_settings,
        agents={**base_settings.agents, "codex": [sys.executable, str(overloaded)]},
    )
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as raised:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert raised.value.reason == (
        "codex hit a temporary capacity error (Selected model is at capacity); "
        "resume in a few minutes or change its model"
    )


def test_a_turn_fails_when_the_volume_has_under_1gb_free(repo, monkeypatch):
    class Usage:
        total = 0
        used = 0
        free = 12 * 1024 * 1024

    monkeypatch.setattr(loop.shutil, "disk_usage", lambda path: Usage())
    settings = config.load(repo)
    with pytest.raises(
        loop.Paused,
        match="disk has 12 MB free; the relay needs at least 1 GB",
    ):
        loop.run_task(repo, settings, TASK, base_commit=loop.gitcheck.head_commit(repo), echo=False)


def test_no_chain_configured_pauses_exactly_as_before(repo, tmp_path):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    base = config.load(repo)
    settings = config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, str(limited)],
            "claude": [
                sys.executable, FAKE, str(repo), "claude", "claude",
                "approved", "yes",
            ],
        },
        status_map=base.status_map,
    )
    base_commit = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as excinfo:
        loop.run_task(repo, settings, TASK, base_commit=base_commit, echo=False)
    assert "hit a usage or rate limit" in str(excinfo.value)
    assert "every backup in the chain is unavailable" not in str(excinfo.value)


def fake(root, frm, to, status, commit):
    return [sys.executable, FAKE, str(root), frm, to, status, commit]


def model_settings(root, codex, claude, chain=()):
    """claude configured on sonnet, codex on its CLI default."""
    relay = root / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "config.toml").write_text('[agents.claude]\nmodel = "sonnet"\n')
    base = config.load(root)
    return replace(
        base,
        agents={**base.agents, "codex": codex, "claude": claude},
        backup_chain=list(chain),
    )


def model_of(command):
    return command[command.index("--model") + 1] if "--model" in command else ""


def test_a_switch_during_a_turn_is_used_from_the_next_turn_and_echoes_once(
    repo, monkeypatch, capsys
):
    settings = model_settings(
        repo,
        fake(repo, "codex", "claude", "ready-for-review", "no"),
        fake(repo, "claude", "claude", "approved", "yes") + ["--model", "sonnet"],
    )
    real_run = loop.agents.run
    calls = []

    def spy(command, prompt, **kwargs):
        agent = kwargs["agent_name"]
        calls.append((agent, list(command)))
        command = list(command)
        if len(calls) == 1:
            # Both land while codex's first turn is running.
            roles.switch_model(repo, settings, "codex", "o3")
            roles.switch_model(repo, settings, "claude", "opus")
        if agent == "claude" and sum(a == "claude" for a, _ in calls) == 1:
            command[4:7] = ["codex", "changes-requested", "no"]
        return real_run(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", spy)
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=True)
    assert outcome.committed
    assert [(agent, model_of(command)) for agent, command in calls] == [
        ("codex", ""),
        ("claude", "opus"),
        ("codex", "o3"),
        ("claude", "opus"),
    ]
    # The flag's value was replaced, not a second flag appended.
    assert calls[1][1].count("--model") == 1
    out = capsys.readouterr().out
    assert out.count("==> relay: claude now uses opus (switched by you)") == 1
    assert out.count("==> relay: codex now uses o3 (switched by you)") == 1


def test_a_model_switch_applies_to_the_agent_as_reviewer_and_as_backup(
    repo, tmp_path, monkeypatch
):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = model_settings(
        repo,
        [sys.executable, str(limited)],
        fake(repo, "claude", "claude", "approved", "yes") + ["--model", "sonnet"],
        chain=["claude"],
    )
    roles.switch_model(repo, settings, "claude", "opus")
    real_run = loop.agents.run
    calls = []

    def spy(command, prompt, **kwargs):
        calls.append((kwargs["agent_name"], list(command)))
        command = list(command)
        if len(calls) == 2:
            # claude's first turn is the implementer's, as codex's backup.
            command[4:7] = ["claude", "ready-for-review", "no"]
        return real_run(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", spy)
    base = loop.gitcheck.head_commit(repo)
    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert outcome.committed
    assert failover.read_overrides(repo)["implementer"].agent == "claude"
    assert [(agent, model_of(command)) for agent, command in calls] == [
        ("codex", ""),
        ("claude", "opus"),
        ("claude", "opus"),
    ]


def test_a_garbled_model_overrides_file_runs_the_configured_model(
    repo, monkeypatch, capsys
):
    settings = model_settings(
        repo,
        fake(repo, "codex", "claude", "ready-for-review", "no"),
        fake(repo, "claude", "claude", "approved", "yes") + ["--model", "sonnet"],
    )
    garbled = repo / ".whyline" / "relay" / "model-overrides.json"
    garbled.write_text("{not json")
    real_run = loop.agents.run
    calls = []

    def spy(command, prompt, **kwargs):
        calls.append((kwargs["agent_name"], list(command)))
        return real_run(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", spy)
    base = loop.gitcheck.head_commit(repo)
    assert loop.run_task(repo, settings, TASK, base_commit=base, echo=True).committed
    assert [model_of(command) for _, command in calls] == ["", "sonnet"]
    out = capsys.readouterr().out
    assert out.count("model switches ignored; model-overrides.json could not be read") == 1
    assert garbled.read_text() == "{not json"


def test_an_entry_for_an_agent_not_in_this_run_is_ignored_once(
    repo, monkeypatch, capsys
):
    settings = model_settings(
        repo,
        fake(repo, "codex", "claude", "ready-for-review", "no"),
        fake(repo, "claude", "claude", "approved", "yes") + ["--model", "sonnet"],
    )
    target = repo / ".whyline" / "relay" / "model-overrides.json"
    target.write_text(
        json.dumps({"ghost": {"model": "big", "since": "t", "keep": False}})
    )
    base = loop.gitcheck.head_commit(repo)
    assert loop.run_task(repo, settings, TASK, base_commit=base, echo=True).committed
    out = capsys.readouterr().out
    assert out.count("==> relay: ghost model switch to big ignored") == 1
    assert "ghost" in json.loads(target.read_text())


def test_running_json_and_the_turn_log_carry_the_model(repo, monkeypatch):
    settings = model_settings(
        repo,
        fake(repo, "codex", "claude", "ready-for-review", "no"),
        fake(repo, "claude", "claude", "approved", "yes") + ["--model", "sonnet"],
    )
    roles.switch_model(repo, settings, "claude", "opus")
    real_run = loop.agents.run
    markers = []

    def spy(command, prompt, **kwargs):
        markers.append(
            json.loads((repo / ".whyline" / "relay" / "running.json").read_text())
        )
        return real_run(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", spy)
    base = loop.gitcheck.head_commit(repo)
    assert loop.run_task(repo, settings, TASK, base_commit=base, echo=False).committed
    assert [(m["agent"], m["model"]) for m in markers] == [
        ("codex", ""),
        ("claude", "opus"),
    ]
    for agent, model in (("codex", ""), ("claude", "opus")):
        log = loop.log_path(repo, "T-1", 1, agent).read_text().splitlines()
        assert log[0].startswith(loop.TURN_LOG_HEADER)
        record = json.loads(log[0][len(loop.TURN_LOG_HEADER):])
        assert record == {"agent": agent, "model": model}
        assert log[1].startswith("fake-role-agent")


def test_plan_completion_removes_switches_without_keep(repo):
    settings = model_settings(
        repo,
        fake(repo, "codex", "claude", "ready-for-review", "no"),
        fake(repo, "claude", "claude", "approved", "yes"),
    )
    roles.switch_model(repo, settings, "claude", "opus")
    roles.switch_model(repo, settings, "codex", "o3", keep=True)
    (repo / "plan.md").write_text("- [x] T-1: done\n")
    loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)
    remaining = roles.read_model_overrides(repo)
    assert set(remaining) == {"codex"}
    assert 'model = "o3"' in config.config_path(repo).read_text()
