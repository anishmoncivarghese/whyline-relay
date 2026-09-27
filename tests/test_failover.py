import json
import subprocess

from whyline_relay import failover
from whyline_relay.adapters import codex as codex_adapter


def test_no_override_means_the_configured_agent(tmp_path):
    from whyline_relay.config import Config, Roles

    settings = Config(
        plan="p",
        max_rounds=3,
        timeout_minutes=30,
        branch_prefix="r/",
        agents={},
        status_map={},
        roles=Roles("codex", "claude"),
    )
    assert failover.effective_agent(tmp_path, settings, "implementer") == "codex"


def test_a_written_override_is_the_effective_agent(tmp_path):
    from whyline_relay.config import Config, Roles

    settings = Config(
        plan="p",
        max_rounds=3,
        timeout_minutes=30,
        branch_prefix="r/",
        agents={},
        status_map={},
        roles=Roles("codex", "claude"),
    )
    failover.write_override(
        tmp_path,
        "implementer",
        failover.ActiveOverride(
            "antigravity", "codex", "rate-limit", "2026-01-01T00:00:00"
        ),
    )
    assert (
        failover.effective_agent(tmp_path, settings, "implementer") == "antigravity"
    )


def test_clear_overrides_one_role(tmp_path):
    failover.write_override(
        tmp_path,
        "implementer",
        failover.ActiveOverride("a", "b", "rate-limit", "t"),
    )
    failover.write_override(
        tmp_path, "reviewer", failover.ActiveOverride("c", "d", "auth", "t")
    )
    assert failover.clear_overrides(tmp_path, "implementer") == 1
    remaining = failover.read_overrides(tmp_path)
    assert set(remaining) == {"reviewer"}


def test_clear_overrides_all(tmp_path):
    failover.write_override(
        tmp_path,
        "implementer",
        failover.ActiveOverride("a", "b", "rate-limit", "t"),
    )
    assert failover.clear_overrides(tmp_path) == 1
    assert failover.read_overrides(tmp_path) == {}
    assert failover.clear_overrides(tmp_path) == 0


def test_still_logged_in_true_for_a_generic_adapter():
    from whyline_relay.adapters.generic import ADAPTER

    assert failover.still_logged_in(ADAPTER) is True


def test_still_logged_in_reads_the_login_check_exit_code():
    ok = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    bad = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "")
    assert failover.still_logged_in(codex_adapter.ADAPTER, runner=ok) is True
    assert failover.still_logged_in(codex_adapter.ADAPTER, runner=bad) is False


def test_still_logged_in_never_guesses_logged_out_on_a_broken_check():
    def explode(*a, **k):
        raise OSError("no such program")

    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=20)

    assert failover.still_logged_in(codex_adapter.ADAPTER, runner=explode) is True
    assert failover.still_logged_in(codex_adapter.ADAPTER, runner=hang) is True


def test_failover_reason_prefers_rate_limit_over_auth():
    bad = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "")
    assert (
        failover.failover_reason(
            codex_adapter.ADAPTER,
            "you hit your usage limit",
            ["codex"],
            runner=bad,
        )
        == "rate-limit"
    )


def test_failover_reason_is_none_when_all_clear():
    ok = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
    assert (
        failover.failover_reason(
            codex_adapter.ADAPTER, "ordinary output", ["codex"], runner=ok
        )
        is None
    )


def test_failover_reason_generic_agent_never_reports_auth():
    from whyline_relay.adapters.generic import ADAPTER

    assert (
        failover.failover_reason(ADAPTER, "totally ordinary text", ["aider"])
        is None
    )


def test_cancelled_grok_reasoning_cannot_fake_a_rate_limit():
    from whyline_relay.adapters.generic import ADAPTER

    captured = json.dumps(
        {
            "text": "I will inspect the repository first.",
            "stopReason": "cancelled",
            "thought": "The task asks me to document a usage limit and rate limit.",
        },
        indent=2,
    )
    assert failover.rate_limited(ADAPTER, captured, ["grok", "-p"]) is False
    assert failover.failover_reason(ADAPTER, captured, ["grok", "-p"]) is None


def test_non_cancelled_grok_final_text_can_report_a_real_rate_limit():
    from whyline_relay.adapters.generic import ADAPTER

    captured = json.dumps(
        {
            "text": "You have exceeded your usage limit. Try again later.",
            "stopReason": "end_turn",
            "thought": "The request failed before any tools ran.",
        }
    )
    assert failover.rate_limited(ADAPTER, captured, ["grok", "-p"]) is True
    assert (
        failover.failover_reason(ADAPTER, captured, ["grok", "-p"])
        == "rate-limit"
    )


def test_other_generic_agents_keep_legacy_text_rate_limit_detection():
    from whyline_relay.adapters.generic import ADAPTER

    assert (
        failover.failover_reason(
            ADAPTER, "You have exceeded your usage limit.", ["aider"]
        )
        == "rate-limit"
    )


def test_failover_reason_never_login_checks_a_stand_in_command():
    # command[0] is not literally "codex", so the auth check must never run, matching
    # preflight._logins's own rule for the same situation.
    def explode(*a, **k):
        raise AssertionError("must not run a login check on a stand-in")

    assert (
        failover.failover_reason(
            codex_adapter.ADAPTER,
            "ordinary output",
            ["python3", "fake.py"],
            runner=explode,
        )
        is None
    )


def test_pause_message_matches_0_2_3_exactly_with_no_override():
    assert failover.pause_message("codex", "implementer", "rate-limit", None) == (
        "codex hit a usage or rate limit; try again when it resets"
    )


def test_pause_message_for_auth_with_no_override():
    assert failover.pause_message("claude", "reviewer", "auth", None) == (
        "claude is no longer logged in; try again once you've signed back in"
    )


def test_pause_message_when_the_backup_also_failed():
    override = failover.ActiveOverride("antigravity", "codex", "rate-limit", "t")
    message = failover.pause_message(
        "antigravity", "implementer", "rate-limit", override
    )
    assert "backup for implementer" in message
    assert "codex was already out" in message
    assert "hit a usage or rate limit" in message


def test_active_roles_json_is_locally_excluded(tmp_path):
    from whyline_relay import gitcheck

    assert ".whyline/relay/active-roles.json" in gitcheck.RELAY_IGNORE


def test_chat_path_is_separate_from_the_pipeline_path(tmp_path):
    assert failover.chat_path(tmp_path) != failover.path(tmp_path)
    assert failover.chat_path(tmp_path).name == "chat-active-agents.json"


def test_writing_to_the_chat_path_does_not_touch_the_pipeline_file(tmp_path):
    failover.write_override(
        tmp_path, "claude",
        failover.ActiveOverride("codex", "claude", "rate-limit", "t"),
        storage_path=failover.chat_path(tmp_path),
    )
    assert failover.read_overrides(tmp_path) == {}
    assert failover.read_overrides(tmp_path, failover.chat_path(tmp_path)) == {
        "claude": failover.ActiveOverride("codex", "claude", "rate-limit", "t")
    }


def test_clear_overrides_respects_an_explicit_storage_path(tmp_path):
    chat_file = failover.chat_path(tmp_path)
    failover.write_override(
        tmp_path, "claude",
        failover.ActiveOverride("codex", "claude", "rate-limit", "t"),
        storage_path=chat_file,
    )
    failover.write_override(
        tmp_path, "implementer",
        failover.ActiveOverride("claude", "codex", "auth", "t"),
    )
    removed = failover.clear_overrides(tmp_path, storage_path=chat_file)
    assert removed == 1
    assert failover.read_overrides(tmp_path, chat_file) == {}
    assert "implementer" in failover.read_overrides(tmp_path)


def test_resolve_chat_agent_returns_the_requested_name_with_no_override(tmp_path):
    assert failover.resolve_chat_agent(tmp_path, "claude") == "claude"


def test_resolve_chat_agent_returns_the_backup_when_overridden(tmp_path):
    failover.write_override(
        tmp_path, "claude",
        failover.ActiveOverride("codex", "claude", "rate-limit", "t"),
        storage_path=failover.chat_path(tmp_path),
    )
    assert failover.resolve_chat_agent(tmp_path, "claude") == "codex"
