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


def test_set_changes_the_agent_and_reports_it(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert (
        cli.main(
            ["roles", "set", "reviewer", "--agent", "codex", "--repo", str(repo)]
        )
        == 0
    )
    assert "reviewer set to codex" in capsys.readouterr().out
    capsys.readouterr()
    cli.main(["roles", "status", "--repo", str(repo)])
    assert "reviewer: codex" in capsys.readouterr().out


def test_set_with_a_model_reports_it_too(tmp_path, capsys):
    repo = make_repo(tmp_path)
    code = cli.main(
        [
            "roles",
            "set",
            "implementer",
            "--agent",
            "claude",
            "--model",
            "opus",
            "--repo",
            str(repo),
        ]
    )
    assert code == 0
    assert "implementer set to claude (model opus)" in capsys.readouterr().out


def test_set_reports_an_error_and_exits_nonzero_on_an_unknown_agent(
    tmp_path, capsys
):
    repo = make_repo(tmp_path)
    code = cli.main(
        [
            "roles",
            "set",
            "implementer",
            "--agent",
            "nonexistent-thing",
            "--repo",
            str(repo),
        ]
    )
    assert code == 1
    assert "not a built-in agent" in capsys.readouterr().err


def test_reset_accepts_a_role_a_configured_pipeline_actually_defines(
    tmp_path, capsys
):
    # A pipeline's own role name (here, "tester") -- reset could not accept
    # anything but "implementer"/"reviewer" before this task.
    repo = make_repo(tmp_path)
    (repo / ".whyline" / "relay" / "config.toml").write_text(
        '[roles]\nimplementer = "codex"\ntester = "claude"\nreviewer = "claude"\n'
        '[pipeline]\ndefault_profile = "full"\n'
        '[pipeline.profiles]\nfull = ["only"]\n'
        '[pipeline.stages.only]\nrole = "implementer"\nprompt = "implement"\n'
        '[pipeline.stages.only.on]\nready = "@complete"\n'
    )
    code = cli.main(["roles", "reset", "tester", "--repo", str(repo)])
    assert code == 0
    assert "was not on a backup" in capsys.readouterr().out


def test_reset_rejects_a_role_the_current_config_does_not_define(tmp_path, capsys):
    repo = make_repo(tmp_path)
    code = cli.main(["roles", "reset", "nonexistent", "--repo", str(repo)])
    assert code == 1
    assert "not a configured role" in capsys.readouterr().err
