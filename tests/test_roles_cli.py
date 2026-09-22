from pathlib import Path

from whyline_relay import cli, failover


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / ".whyline" / "relay").mkdir(parents=True)
    (tmp_path / ".whyline" / "relay" / "config.toml").write_text("")
    return tmp_path


def test_status_with_no_override_shows_only_configured_agents(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert cli.main(["roles", "status", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "implementer: codex" in out and "reviewer: claude" in out
    assert "currently" not in out


def test_status_shows_an_active_override(tmp_path, capsys):
    repo = make_repo(tmp_path)
    failover.write_override(
        repo,
        "implementer",
        failover.ActiveOverride(
            "antigravity", "codex", "rate-limit", "2026-01-01T00:00:00"
        ),
    )
    cli.main(["roles", "status", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert "currently antigravity" in out and "rate-limit" in out


def test_reset_one_role(tmp_path, capsys):
    repo = make_repo(tmp_path)
    failover.write_override(
        repo,
        "implementer",
        failover.ActiveOverride("a", "b", "rate-limit", "t"),
    )
    assert cli.main(["roles", "reset", "implementer", "--repo", str(repo)]) == 0
    assert failover.read_overrides(repo) == {}
    assert "reset" in capsys.readouterr().out.lower()


def test_reset_all(tmp_path, capsys):
    repo = make_repo(tmp_path)
    failover.write_override(
        repo,
        "implementer",
        failover.ActiveOverride("a", "b", "rate-limit", "t"),
    )
    failover.write_override(
        repo,
        "reviewer",
        failover.ActiveOverride("c", "d", "auth", "t"),
    )
    assert cli.main(["roles", "reset", "--repo", str(repo)]) == 0
    assert failover.read_overrides(repo) == {}


def test_reset_with_nothing_to_reset_says_so(tmp_path, capsys):
    repo = make_repo(tmp_path)
    cli.main(["roles", "reset", "--repo", str(repo)])
    assert "nothing to reset" in capsys.readouterr().out.lower()
