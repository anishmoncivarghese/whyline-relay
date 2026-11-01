"""`roles switch`: a switch the user makes while the relay runs (RLY-13)."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from whyline_relay import cli, config, failover, loop, plan, roles


FAKE = str(Path(__file__).parent / "fake_role_agent.py")
TASK = plan.Task(task_id="T-1", text="T-1: Work", checked=False, line_index=0)

RATE_LIMITED = '''#!/usr/bin/env python3
import sys
print("You have exceeded your usage limit.")
sys.exit(1)
'''


def _manual(agent: str, backup_for: str, *, keep: bool = False) -> failover.ActiveOverride:
    return failover.ActiveOverride(
        agent=agent,
        backup_for=backup_for,
        reason="manual",
        since="2026-11-01T10:00:00+00:00",
        tried=[],
        keep=keep,
    )


# --- roles.switch -----------------------------------------------------------


def test_switch_to_another_agent_writes_a_manual_override(tmp_path):
    settings = config.load(tmp_path)

    message = roles.switch(tmp_path, settings, "reviewer", "codex")

    assert message == "reviewer → codex from its next turn."
    override = failover.read_overrides(tmp_path)["reviewer"]
    assert (override.agent, override.backup_for, override.reason) == (
        "codex",
        "claude",
        "manual",
    )
    assert override.tried == [] and override.keep is False
    assert failover.effective_agent(tmp_path, settings, "reviewer") == "codex"


def test_switch_keep_is_stored(tmp_path):
    settings = config.load(tmp_path)
    roles.switch(tmp_path, settings, "reviewer", "codex", keep=True)
    assert failover.read_overrides(tmp_path)["reviewer"].keep is True


def test_switch_to_the_configured_agent_clears_the_override(tmp_path):
    settings = config.load(tmp_path)
    roles.switch(tmp_path, settings, "reviewer", "codex")

    message = roles.switch(tmp_path, settings, "reviewer", "claude")

    assert message == "reviewer back to claude from its next turn."
    assert failover.read_overrides(tmp_path) == {}
    assert failover.effective_agent(tmp_path, settings, "reviewer") == "claude"


def test_switch_never_touches_config_toml(tmp_path):
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text('[roles]\nreviewer = "claude"\n')
    settings = config.load(tmp_path)
    roles.switch(tmp_path, settings, "reviewer", "codex")
    assert (relay / "config.toml").read_text() == '[roles]\nreviewer = "claude"\n'


def test_switch_refuses_an_unknown_agent(tmp_path):
    settings = config.load(tmp_path)
    with pytest.raises(roles.RoleSetError, match="^ghost is not set up for this relay$"):
        roles.switch(tmp_path, settings, "reviewer", "ghost")
    assert failover.read_overrides(tmp_path) == {}


def test_switch_refuses_an_unknown_role(tmp_path):
    settings = config.load(tmp_path)
    with pytest.raises(roles.RoleSetError, match="not a configured role"):
        roles.switch(tmp_path, settings, "tester", "codex")


def test_old_override_files_without_keep_still_read(tmp_path):
    target = failover.path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "implementer": {
                    "agent": "claude",
                    "backup_for": "codex",
                    "reason": "rate-limit",
                    "since": "2026-01-01T00:00:00+00:00",
                }
            }
        )
    )
    assert failover.read_overrides(tmp_path)["implementer"].keep is False


# --- CLI ----------------------------------------------------------------------


def test_cli_switch_prints_the_message_and_exits_0(tmp_path, capsys):
    code = cli.main(["roles", "--repo", str(tmp_path), "switch", "reviewer", "codex", "--keep"])
    assert code == 0
    assert capsys.readouterr().out == "reviewer → codex from its next turn.\n"
    assert failover.read_overrides(tmp_path)["reviewer"].keep is True


def test_cli_switch_prints_the_error_and_exits_2(tmp_path, capsys):
    code = cli.main(["roles", "--repo", str(tmp_path), "switch", "reviewer", "ghost"])
    assert code == 2
    assert "ghost is not set up for this relay" in capsys.readouterr().err


def test_roles_status_shows_a_manual_switch(tmp_path):
    settings = config.load(tmp_path)
    failover.write_override(tmp_path, "reviewer", _manual("codex", "claude"))
    assert roles.status(tmp_path, settings) == (
        "implementer: codex\n"
        "reviewer: claude, currently codex "
        "(switched by you, since 2026-11-01T10:00:00+00:00)"
    )


def test_roles_status_shows_an_unknown_reason_as_the_raw_word(tmp_path):
    settings = config.load(tmp_path)
    failover.write_override(
        tmp_path,
        "reviewer",
        failover.ActiveOverride("codex", "claude", "solar-flare", "2026-11-01"),
    )
    assert roles.status(tmp_path, settings).endswith(
        "reviewer: claude, currently codex "
        "(solar-flare: claude solar-flare, since 2026-11-01)"
    )


# --- pause_message --------------------------------------------------------------


def test_pause_message_for_a_manually_switched_agent():
    message = failover.pause_message(
        "codex", "reviewer", "rate-limit", _manual("codex", "claude")
    )
    assert message == (
        "codex (switched in by you for reviewer, in place of claude) "
        "hit a usage or rate limit; try again when it resets"
    )


def test_pause_message_with_an_unknown_reason_word_does_not_raise():
    override = failover.ActiveOverride("codex", "claude", "solar-flare", "2026-11-01")
    assert failover.pause_message("codex", "reviewer", "rate-limit", override) == (
        "codex (backup for reviewer; claude was already out: it solar-flare) "
        "also hit a usage or rate limit; try again when it resets"
    )
    assert failover.pause_message("codex", "reviewer", "solar-flare", None) == (
        "codex solar-flare; try again later"
    )


def test_a_manual_switch_is_never_recorded_as_an_agent_limit(tmp_path):
    assert failover.record_limit(tmp_path, "codex", "manual", "text") is None
    assert failover.agent_limits(tmp_path) == {}


# --- the loop -------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "relay/plan")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "README.md").write_text("x\n")
    (tmp_path / ".whyline").mkdir()
    (tmp_path / ".whyline" / ".gitignore").write_text(
        "active-handoff.json\nledger.jsonl\n"
    )
    git("add", "-A")
    git("commit", "-m", "initial")
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda *a, **k: "PACKET")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    return tmp_path


def _fake(root, from_actor, to_actor, status, commit):
    return [sys.executable, FAKE, str(root), from_actor, to_actor, status, commit]


def _settings(root, agents, *, chain=()):
    base = config.load(root)
    return replace(
        base,
        agents=agents,
        adapters={name: "generic" for name in agents if name not in ("codex", "claude")},
        backup_chain=list(chain),
    )


def _record_turns(monkeypatch, before=None):
    turns = []
    real_run = loop.agents.run

    def run(command, prompt, **kwargs):
        turns.append(kwargs["agent_name"])
        if before is not None:
            before(len(turns), kwargs["agent_name"])
        return real_run(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", run)
    return turns


def test_a_switch_made_mid_turn_applies_from_the_roles_next_turn(
    repo, tmp_path_factory, monkeypatch, capsys
):
    counter = tmp_path_factory.mktemp("count") / "reviews"
    reviewer = tmp_path_factory.mktemp("script") / "reviewer.py"
    reviewer.write_text(
        "import subprocess, sys\n"
        "from pathlib import Path\n"
        f"count = Path({str(counter)!r})\n"
        "n = int(count.read_text()) if count.exists() else 0\n"
        "count.write_text(str(n + 1))\n"
        "args = ['claude', 'aider', 'changes-requested', 'no'] if n == 0 "
        "else ['claude', 'aider', 'approved', 'yes']\n"
        f"sys.exit(subprocess.call([sys.executable, {FAKE!r}, sys.argv[1], *args, sys.argv[-1]]))\n"
    )
    settings = _settings(
        repo,
        {
            "codex": _fake(repo, "codex", "claude", "ready-for-review", "no"),
            "claude": [sys.executable, str(reviewer), str(repo)],
            "aider": _fake(repo, "aider", "claude", "ready-for-review", "no"),
        },
    )

    def switch_during_first_turn(turn, agent):
        if turn == 1:
            # The relay has already chosen codex for this turn.
            roles.switch(repo, settings, "implementer", "aider")

    turns = _record_turns(monkeypatch, switch_during_first_turn)
    base = loop.gitcheck.head_commit(repo)

    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=True)

    assert outcome.committed
    # codex's own turn ran and was accepted as codex's; the next implementer turn is aider.
    assert turns == ["codex", "claude", "aider", "claude"]
    out = capsys.readouterr().out
    assert out.count("==> relay: implementer now aider (switched by you)") == 1


def test_a_manually_switched_agent_hitting_a_limit_fails_over_through_the_chain(
    repo, tmp_path_factory, monkeypatch
):
    limited = tmp_path_factory.mktemp("script") / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = _settings(
        repo,
        {
            "codex": _fake(repo, "codex", "claude", "ready-for-review", "no"),
            "claude": _fake(repo, "claude", "aider", "approved", "yes"),
            "limited": [sys.executable, str(limited)],
            "aider": _fake(repo, "aider", "claude", "ready-for-review", "no"),
        },
        chain=["codex", "aider"],
    )
    roles.switch(repo, settings, "implementer", "limited")
    turns = _record_turns(monkeypatch)
    base = loop.gitcheck.head_commit(repo)

    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=False)

    assert outcome.committed
    # The configured codex is skipped as if it had been the one that failed.
    assert turns == ["limited", "aider", "claude"]
    override = failover.read_overrides(repo)["implementer"]
    assert (override.agent, override.backup_for, override.reason) == (
        "aider",
        "limited",
        "rate-limit",
    )
    assert override.tried == ["limited", "codex"]


def test_a_manual_agent_with_no_chain_pauses_naming_your_switch(
    repo, tmp_path_factory
):
    limited = tmp_path_factory.mktemp("script") / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = _settings(
        repo,
        {
            "codex": _fake(repo, "codex", "claude", "ready-for-review", "no"),
            "claude": _fake(repo, "claude", "codex", "approved", "yes"),
            "limited": [sys.executable, str(limited)],
        },
    )
    roles.switch(repo, settings, "implementer", "limited")
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as paused:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    assert paused.value.reason == (
        "limited (switched in by you for implementer, in place of codex) "
        "hit a usage or rate limit; try again when it resets"
    )


def test_an_override_naming_an_agent_not_set_up_is_ignored_with_an_echo(
    repo, monkeypatch, capsys
):
    settings = _settings(
        repo,
        {
            "codex": _fake(repo, "codex", "claude", "ready-for-review", "no"),
            "claude": _fake(repo, "claude", "codex", "approved", "yes"),
        },
    )
    # Hand-edited: roles.switch would refuse this agent.
    failover.write_override(repo, "implementer", _manual("ghost", "codex"))
    turns = _record_turns(monkeypatch)
    base = loop.gitcheck.head_commit(repo)

    outcome = loop.run_task(repo, settings, TASK, base_commit=base, echo=True)

    assert outcome.committed
    assert turns == ["codex", "claude"]
    assert (
        "==> relay: implementer switch to ghost ignored; "
        "it was not set up when this run started"
    ) in capsys.readouterr().out
    assert failover.read_overrides(repo)["implementer"].agent == "ghost"


def _commit_plan(root, text):
    (root / "plan.md").write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "plan"], cwd=root, check=True, capture_output=True)


def test_plan_completion_drops_non_keep_manual_switches_and_keeps_the_rest(repo):
    _commit_plan(repo, "- [x] WL-1: Done\n")
    settings = config.load(repo)
    failover.write_override(repo, "reviewer", _manual("codex", "claude"))
    failover.write_override(repo, "implementer", _manual("claude", "codex", keep=True))

    assert loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False) == []

    assert set(failover.read_overrides(repo)) == {"implementer"}


def test_plan_completion_leaves_a_backup_switch_alone(repo):
    _commit_plan(repo, "- [x] WL-1: Done\n")
    settings = config.load(repo)
    failover.write_override(
        repo,
        "reviewer",
        failover.ActiveOverride("codex", "claude", "rate-limit", "2026-11-01"),
    )
    loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)
    assert set(failover.read_overrides(repo)) == {"reviewer"}


def test_a_pause_keeps_every_manual_switch(repo):
    _commit_plan(repo, "- [ ] WL-1: One\n")
    silent = [sys.executable, "-c", "import sys; sys.exit(0)"]
    settings = _settings(repo, {"codex": silent, "claude": silent})
    failover.write_override(repo, "reviewer", _manual("codex", "claude"))
    failover.write_override(repo, "implementer", _manual("claude", "codex", keep=True))

    with pytest.raises(loop.Paused):
        loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)

    assert set(failover.read_overrides(repo)) == {"implementer", "reviewer"}


def test_run_start_drops_a_manual_switch_the_configured_agent_caught_up_with(repo):
    _commit_plan(repo, "- [x] WL-1: Done\n")
    settings = config.load(repo)
    # Kept, so plan completion would leave it: only the run-start check drops it.
    failover.write_override(repo, "reviewer", _manual("claude", "codex", keep=True))
    failover.write_override(repo, "implementer", _manual("claude", "codex", keep=True))

    loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)

    assert set(failover.read_overrides(repo)) == {"implementer"}


# --- a switch written while the old agent's turn runs ------------------------------

OVERLOADED = "Selected model is at capacity\n"
USAGE_LIMIT = "You have exceeded your usage limit.\n"


def _failing_first_turn(monkeypatch, text, delegate, during=None):
    """The first turn fails with `text`, calling `during` while it runs. Every
    later turn goes to `delegate`. Returns the agents run, in order."""
    turns = []

    def run(command, prompt, **kwargs):
        turns.append(kwargs["agent_name"])
        if len(turns) > 1:
            return delegate(command, prompt, **kwargs)
        if during is not None:
            during()
        kwargs["log_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["log_path"].write_text(text)
        return 1

    monkeypatch.setattr(loop.agents, "run", run)
    return turns


def _driver(repo, driver):
    """Settings, the delegate for turns after the first, and the turns expected
    when implementer moves from codex to aider after the first turn."""
    if driver == "legacy":
        settings = _settings(
            repo,
            {
                "codex": _fake(repo, "codex", "claude", "ready-for-review", "no"),
                "claude": _fake(repo, "claude", "aider", "approved", "yes"),
                "aider": _fake(repo, "aider", "claude", "ready-for-review", "no"),
                "grok": _fake(repo, "grok", "claude", "ready-for-review", "no"),
            },
            chain=["grok"],
        )
        return settings, loop.agents.run, ["codex", "aider", "claude"]
    from test_loop_pipeline import _scripted_run, make_pipeline, settings_with_pipeline

    settings = replace(
        settings_with_pipeline(repo, make_pipeline()),
        agents={name: [name] for name in ("codex", "claude", "aider", "grok")},
        adapters={"aider": "generic", "grok": "generic"},
        backup_chain=["grok"],
    )
    delegate = _scripted_run(["claude:ready", "claude:passed", "claude:approved"])
    return settings, delegate, ["codex", "aider", "claude", "claude"]


@pytest.mark.parametrize("driver", ["legacy", "configured"])
@pytest.mark.parametrize("text", [OVERLOADED, USAGE_LIMIT], ids=["overload", "rate-limit"])
def test_a_switch_during_a_failing_turn_wins_over_its_failover(
    repo, monkeypatch, capsys, driver, text
):
    settings, delegate, expected = _driver(repo, driver)
    turns = _failing_first_turn(
        monkeypatch,
        text,
        delegate,
        during=lambda: roles.switch(repo, settings, "implementer", "aider"),
    )

    outcome = loop.run_task(
        repo, settings, TASK, base_commit=loop.gitcheck.head_commit(repo), echo=True
    )

    assert outcome.committed
    # Not grok: neither the one-turn overload fallback nor a sticky backup.
    assert turns == expected
    override = failover.read_overrides(repo)["implementer"]
    assert (override.agent, override.reason) == ("aider", "manual")
    assert "implementer follows your switch from its next turn" in capsys.readouterr().out


def test_a_rate_limit_during_a_switch_is_still_remembered(repo, monkeypatch):
    settings, delegate, _ = _driver(repo, "legacy")
    _failing_first_turn(
        monkeypatch,
        USAGE_LIMIT,
        delegate,
        during=lambda: roles.switch(repo, settings, "implementer", "aider"),
    )
    loop.run_task(
        repo, settings, TASK, base_commit=loop.gitcheck.head_commit(repo), echo=False
    )
    assert "codex" in failover.limited_agents(repo)


@pytest.mark.parametrize("driver", ["legacy", "configured"])
def test_a_switch_after_an_overload_drops_the_queued_fallback(repo, monkeypatch, driver):
    settings, delegate, expected = _driver(repo, driver)
    turns = _failing_first_turn(monkeypatch, OVERLOADED, delegate)
    real = loop._failover_no_handoff

    def failover_then_switch(*args, **kwargs):
        # grok is queued for the next turn by now; you switch before it starts.
        result = real(*args, **kwargs)
        roles.switch(repo, settings, "implementer", "aider")
        return result

    monkeypatch.setattr(loop, "_failover_no_handoff", failover_then_switch)

    outcome = loop.run_task(
        repo, settings, TASK, base_commit=loop.gitcheck.head_commit(repo), echo=False
    )

    assert outcome.committed
    assert turns == expected


@pytest.mark.parametrize("driver", ["legacy", "configured"])
def test_an_overload_with_no_switch_still_uses_the_fallback(repo, monkeypatch, driver):
    settings, delegate, expected = _driver(repo, driver)
    turns = _failing_first_turn(monkeypatch, OVERLOADED, delegate)

    outcome = loop.run_task(
        repo, settings, TASK, base_commit=loop.gitcheck.head_commit(repo), echo=False
    )

    assert outcome.committed
    assert turns == ["codex", "grok", *expected[2:]]
    assert failover.read_overrides(repo) == {}
