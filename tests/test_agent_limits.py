import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from whyline_relay import agents, failover, loop

from test_loop_failover import (  # noqa: F401  (repo is a fixture)
    FAKE,
    OVERLOAD,
    RATE_LIMITED,
    TASK,
    repo,
    settings_with_backup,
)


NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


def _relay_dir(root):
    (root / ".whyline" / "relay").mkdir(parents=True, exist_ok=True)
    return root


def _spy_runs(monkeypatch):
    seen = []
    real_run = loop.agents.run

    def spy(command, prompt, **kwargs):
        seen.append(kwargs.get("agent_name"))
        return real_run(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", spy)
    return seen


def _ready(root, agent):
    return [sys.executable, FAKE, str(root), agent, "claude", "ready-for-review", "no"]


def test_a_429_with_a_stated_reset_records_that_deadline(tmp_path):
    root = _relay_dir(tmp_path)
    text = (
        "working...\n"
        "RESOURCE_EXHAUSTED (code 429): Individual quota reached. "
        "Resets in 159h57m31s.\n\n"
    )
    limit = failover.record_limit(root, "agy", "rate-limit", text, now=NOW)
    expected = NOW + timedelta(hours=159, minutes=57, seconds=31)
    assert limit is not None
    assert datetime.fromisoformat(limit.resets_at) == expected
    assert limit.detail.startswith("RESOURCE_EXHAUSTED (code 429)")
    stored = json.loads(failover.limits_path(root).read_text())
    assert stored["agy"] == {
        "reason": "rate-limit",
        "since": NOW.isoformat(),
        "resets_at": expected.isoformat(),
        "detail": limit.detail,
    }
    assert set(failover.agent_limits(root, now=NOW)) == {"agy"}


def test_the_detail_is_capped_at_160_characters(tmp_path):
    root = _relay_dir(tmp_path)
    limit = failover.record_limit(root, "a", "rate-limit", "x" * 500 + "\n", now=NOW)
    assert limit.detail == "x" * 160


def test_a_past_deadline_is_dropped_and_rewritten_away(tmp_path):
    root = _relay_dir(tmp_path)
    failover.record_limit(root, "old", "rate-limit", "Resets in 1h0m", now=NOW)
    failover.record_limit(root, "new", "rate-limit", "Resets in 9h0m", now=NOW)
    later = NOW + timedelta(hours=2)
    assert set(failover.agent_limits(root, now=later)) == {"new"}
    assert set(json.loads(failover.limits_path(root).read_text())) == {"new"}
    assert failover.agent_limits(root, now=NOW + timedelta(hours=10)) == {}
    assert not failover.limits_path(root).exists()


def test_a_402_balance_message_records_a_rate_limit_lasting_24_hours(tmp_path):
    root = _relay_dir(tmp_path)
    text = "402 Payment Required: Grok Build usage balance exhausted\n"
    assert agents.rate_limited(text)
    limit = failover.record_limit(root, "grok", "rate-limit", text, now=NOW)
    assert limit.reason == "rate-limit"
    assert limit.resets_at == ""
    assert limit.expires_at() == NOW + timedelta(hours=24)
    assert "grok" in failover.agent_limits(root, now=NOW + timedelta(hours=23))
    assert failover.agent_limits(root, now=NOW + timedelta(hours=25)) == {}


def test_an_auth_record_lasts_until_preflight_sees_the_agent_logged_in(tmp_path):
    root = _relay_dir(tmp_path)
    failover.record_limit(root, "claude", "auth", "Please run /login", now=NOW)
    failover.record_limit(root, "codex", "rate-limit", "usage limit", now=NOW)
    assert failover.clear_auth_limit(root, "codex") is False  # not an auth record
    far = NOW + timedelta(days=30)
    assert set(failover.agent_limits(root, now=far)) == {"claude"}
    assert failover.clear_auth_limit(root, "claude") is True
    assert failover.agent_limits(root, now=NOW) == {}


def test_overload_records_nothing(repo, tmp_path):
    assert failover.record_limit(repo, "codex", "overload", "at capacity") is None
    overloaded = tmp_path / "over.py"
    overloaded.write_text(OVERLOAD)
    settings = settings_with_backup(
        repo, [sys.executable, str(overloaded)], "aider", _ready(repo, "aider"), ["aider"]
    )
    base = loop.gitcheck.head_commit(repo)
    assert loop.run_task(repo, settings, TASK, base_commit=base, echo=False).committed
    assert not failover.limits_path(repo).exists()


def test_a_rate_limit_switch_records_the_limited_agent(repo, tmp_path):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = settings_with_backup(
        repo, [sys.executable, str(limited)], "aider", _ready(repo, "aider"), ["aider"]
    )
    base = loop.gitcheck.head_commit(repo)
    assert loop.run_task(repo, settings, TASK, base_commit=base, echo=False).committed
    limits = failover.agent_limits(repo)
    assert set(limits) == {"codex"}
    assert limits["codex"].reason == "rate-limit"
    assert limits["codex"].detail == "You have exceeded your usage limit."


def test_the_chain_skips_a_limited_backup_without_running_it(
    repo, tmp_path, monkeypatch
):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = settings_with_backup(
        repo, [sys.executable, str(limited)], "aider", _ready(repo, "aider"), ["aider", "cline"]
    )
    settings.agents["cline"] = _ready(repo, "cline")
    settings.adapters["cline"] = "generic"
    failover.record_limit(repo, "aider", "rate-limit", "Resets in 5h0m")
    seen = _spy_runs(monkeypatch)
    base = loop.gitcheck.head_commit(repo)
    assert loop.run_task(repo, settings, TASK, base_commit=base, echo=False).committed
    assert "aider" not in seen
    assert seen[:2] == ["codex", "cline"]
    assert failover.read_overrides(repo)["implementer"].agent == "cline"


def test_a_successful_turn_clears_the_agents_record(repo):
    settings = settings_with_backup(
        repo, _ready(repo, "codex"), "aider", _ready(repo, "aider"), ["aider"]
    )
    failover.record_limit(repo, "codex", "rate-limit", "Resets in 5h0m")
    failover.record_limit(repo, "aider", "rate-limit", "Resets in 5h0m")
    base = loop.gitcheck.head_commit(repo)
    assert loop.run_task(repo, settings, TASK, base_commit=base, echo=False).committed
    assert set(failover.agent_limits(repo)) == {"aider"}


def test_every_backup_pause_still_fires_when_every_chain_entry_is_limited(
    repo, tmp_path, monkeypatch
):
    limited = tmp_path / "limited.py"
    limited.write_text(RATE_LIMITED)
    settings = settings_with_backup(
        repo, [sys.executable, str(limited)], "aider", _ready(repo, "aider"), ["aider"]
    )
    failover.record_limit(repo, "aider", "rate-limit", "Resets in 5h0m")
    seen = _spy_runs(monkeypatch)
    base = loop.gitcheck.head_commit(repo)
    with pytest.raises(loop.Paused) as excinfo:
        loop.run_task(repo, settings, TASK, base_commit=base, echo=False)
    message = str(excinfo.value)
    assert "every backup in the chain is unavailable" in message
    assert "aider still limited" in message
    assert seen == ["codex"]
    assert set(failover.agent_limits(repo)) == {"codex", "aider"}
