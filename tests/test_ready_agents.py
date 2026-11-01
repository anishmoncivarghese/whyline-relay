import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from whyline_relay import cli, config, failover

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def make_repo(tmp_path: Path, text: str = "") -> Path:
    (tmp_path / ".whyline" / "relay").mkdir(parents=True)
    (tmp_path / ".whyline" / "relay" / "config.toml").write_text(text)
    return tmp_path


def installed(name: str) -> str:
    return f"/bin/{name}"


def logins(logged_out: set[str] = frozenset(), calls: list | None = None):
    """A login-check runner: every program is logged in except `logged_out`."""

    def runner(argv, **kwargs):
        if calls is not None:
            calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if argv[0] in logged_out else 0)

    return runner


def ready(root: Path, **checks) -> dict[str, failover.AgentReadiness]:
    checks.setdefault("which", installed)
    checks.setdefault("runner", logins())
    rows = failover.ready_agents(root, config.load(root), now=NOW, **checks)
    return {row.agent: row for row in rows}


def test_every_agent_is_ready_when_installed_logged_in_and_not_limited(tmp_path):
    repo = make_repo(tmp_path)
    assert list(ready(repo).values()) == [
        failover.AgentReadiness(name, True, "")
        for name in ("codex", "claude", "antigravity", "grok")
    ]


def test_an_agent_not_on_path_is_not_installed(tmp_path):
    repo = make_repo(tmp_path)
    calls: list = []
    rows = ready(
        repo,
        which=lambda name: None if name == "codex" else installed(name),
        runner=logins(calls=calls),
    )
    assert rows["codex"] == failover.AgentReadiness("codex", False, "not installed")
    assert rows["claude"].ready
    assert [argv[0] for argv in calls] == ["claude"]


def test_a_failed_login_check_is_not_logged_in(tmp_path):
    repo = make_repo(tmp_path)
    rows = ready(repo, runner=logins({"claude"}))
    assert rows["claude"] == failover.AgentReadiness("claude", False, "not logged in")
    assert rows["codex"].ready


def test_an_inconclusive_login_check_does_not_mark_the_agent_out(tmp_path):
    repo = make_repo(tmp_path)

    def timing_out(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 20)

    assert all(row.ready for row in ready(repo, runner=timing_out).values())


def test_a_stated_reset_reports_when_the_quota_returns(tmp_path):
    repo = make_repo(tmp_path)
    failover.record_limit(
        repo, "codex", "rate-limit", "Usage limit. Resets in 3h15m", now=NOW
    )
    rows = ready(repo)
    assert rows["codex"] == failover.AgentReadiness(
        "codex", False, "out of quota until 2026-11-01 15:15"
    )


def test_an_unstated_reset_is_reported_as_unknown(tmp_path):
    repo = make_repo(tmp_path)
    failover.record_limit(repo, "claude", "rate-limit", "usage limit reached", now=NOW)
    rows = ready(repo)
    assert rows["claude"] == failover.AgentReadiness(
        "claude", False, "out of quota (reset time unknown)"
    )


def test_a_limited_agent_is_not_ready_even_though_it_is_logged_in(tmp_path):
    repo = make_repo(tmp_path)
    failover.record_limit(
        repo, "codex", "rate-limit", "Resets in 1h0m", now=NOW - timedelta(minutes=5)
    )
    calls: list = []
    rows = ready(repo, runner=logins(calls=calls))
    assert ["codex", "login", "status"] in calls
    assert not rows["codex"].ready
    assert rows["codex"].why.startswith("out of quota until ")


def test_an_expired_limit_no_longer_counts(tmp_path):
    repo = make_repo(tmp_path)
    failover.record_limit(
        repo, "codex", "rate-limit", "Resets in 1h0m", now=NOW - timedelta(hours=2)
    )
    assert ready(repo)["codex"].ready


def test_an_auth_record_is_cleared_once_the_login_check_passes(tmp_path):
    repo = make_repo(tmp_path)
    failover.record_limit(repo, "claude", "auth", "please log in", now=NOW)
    assert ready(repo)["claude"].ready
    assert "claude" not in failover.agent_limits(repo, now=NOW)


def test_an_auth_record_without_a_login_check_is_not_logged_in(tmp_path):
    repo = make_repo(
        tmp_path,
        '[agents.local]\nadapter = "generic"\ncommand = ["local-agent"]\n',
    )
    failover.record_limit(repo, "local", "auth", "not authenticated", now=NOW)
    assert ready(repo)["local"] == failover.AgentReadiness(
        "local", False, "not logged in"
    )


def test_rows_follow_config_order_including_configured_agents(tmp_path):
    repo = make_repo(
        tmp_path,
        '[agents.zeta]\nadapter = "generic"\ncommand = ["zeta-agent"]\n'
        '[agents.alpha]\nadapter = "generic"\ncommand = ["alpha-agent"]\n',
    )
    settings = config.load(repo)
    assert list(ready(repo)) == list(settings.agents) == [
        "codex",
        "claude",
        "zeta",
        "alpha",
        "antigravity",
        "grok",
    ]


def test_a_shared_binary_is_checked_once(tmp_path):
    repo = make_repo(
        tmp_path,
        '[agents.claude-fast]\nadapter = "claude"\ncommand = ["claude", "-p"]\n',
    )
    calls: list = []
    rows = ready(repo, runner=logins(calls=calls))
    assert rows["claude-fast"].ready
    assert [argv[0] for argv in calls] == ["codex", "claude"]


def test_roles_status_prints_a_row_per_agent_after_the_roles(
    tmp_path, capsys, monkeypatch
):
    repo = make_repo(tmp_path)
    failover.record_limit(repo, "codex", "rate-limit", "usage limit reached")
    real = failover.ready_agents
    monkeypatch.setattr(
        failover,
        "ready_agents",
        lambda root, settings: real(
            root, settings, which=installed, runner=logins({"claude"})
        ),
    )
    assert cli.main(["roles", "status", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "implementer: codex",
        "reviewer: claude",
        "agent codex: not ready, out of quota (reset time unknown)",
        "agent claude: not ready, not logged in",
        "agent antigravity: ready",
        "agent grok: ready",
    ]
