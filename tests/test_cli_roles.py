import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from whyline_relay import cli


def write_marker(root: Path, *, agent: str, role: str | None) -> None:
    target = root / ".whyline" / "relay" / "running.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "agent": agent,
        "task": "T-1",
        "round": 1,
        "started": datetime.now().astimezone().isoformat(),
        "pid": os.getpid(),
    }
    if role is not None:
        record["role"] = role
    target.write_text(json.dumps(record), encoding="utf-8")


@pytest.mark.parametrize(
    ("agent", "role", "action"),
    [
        ("gemini", "reviewer", "reviewing"),
        ("gemini", "implementer", "implementing"),
        ("claude", None, "reviewing"),
    ],
)
def test_status_uses_the_marker_role_and_old_marker_fallback(
    tmp_path: Path, capsys, agent: str, role: str | None, action: str
):
    write_marker(tmp_path, agent=agent, role=role)

    assert cli.main(["status", "--repo", str(tmp_path)]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith(f"Running: {agent} {action} T-1")


def test_dry_run_uses_the_configured_implementer(tmp_path: Path, capsys, monkeypatch):
    (tmp_path / ".whyline" / "relay").mkdir(parents=True)
    (tmp_path / ".whyline" / "relay" / "config.toml").write_text(
        '[roles]\nimplementer = "claude"\nreviewer = "codex"\n',
        encoding="utf-8",
    )
    (tmp_path / "plan.md").write_text("- [ ] T-1: Do it\n", encoding="utf-8")
    monkeypatch.setattr(cli.whylinecmd, "sync", lambda *args, **kwargs: "PACKET")

    assert cli.main(["start", "--repo", str(tmp_path), "--dry-run"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    would_run = next(line for line in output.splitlines() if line.startswith("Would run:"))
    assert would_run.startswith("Would run: claude ")
    assert "--from claude --to codex --status ready-for-review" in output


def test_status_while_running_does_not_offer_the_old_pause_as_current(
    tmp_path: Path, capsys
):
    from whyline_relay import state

    state.save(
        tmp_path,
        state.RelayState(
            plan="plan.md",
            branch="relay/x",
            task_id="T-1",
            round=1,
            base_commit="abc",
            paused_reason="agy exceeded 1800s and was terminated",
            log_path="log.txt",
        ),
    )
    write_marker(tmp_path, agent="antigravity", role="implementer")

    assert cli.main(["status", "--repo", str(tmp_path)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("Running: antigravity implementing T-1")
    assert "Paused    " not in out
    assert "Resume with" not in out
    assert "Last pause  agy exceeded 1800s and was terminated" in out


def test_status_when_paused_still_shows_the_pause_and_resume(tmp_path: Path, capsys):
    from whyline_relay import state

    state.save(
        tmp_path,
        state.RelayState(
            plan="plan.md", branch="relay/x", task_id="T-1", round=1,
            base_commit="abc", paused_reason="stopped", log_path="",
        ),
    )
    assert cli.main(["status", "--repo", str(tmp_path)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Paused    stopped" in out
    assert "Resume with" in out


def _sonnet_config(root: Path) -> Path:
    relay = root / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "config.toml").write_text('[agents.claude]\nmodel = "sonnet"\n')
    return relay


def test_roles_model_switches_and_status_shows_it(tmp_path: Path, capsys):
    _sonnet_config(tmp_path)
    repo = ["--repo", str(tmp_path)]
    assert cli.main(["roles", *repo, "model", "claude", "opus"]) == cli.EXIT_OK
    assert capsys.readouterr().out == "claude → opus from its next turn.\n"

    assert cli.main(["roles", *repo, "status"]) == cli.EXIT_OK
    assert "claude: opus (switched from sonnet)" in capsys.readouterr().out.splitlines()

    assert cli.main(["status", *repo]) == cli.EXIT_OK
    assert "claude: opus (switched from sonnet)" in capsys.readouterr().out.splitlines()


def test_roles_model_reset_removes_the_entry(tmp_path: Path, capsys):
    relay = _sonnet_config(tmp_path)
    repo = ["--repo", str(tmp_path)]
    cli.main(["roles", *repo, "model", "claude", "opus"])
    capsys.readouterr()
    assert cli.main(["roles", *repo, "model", "claude", "--reset"]) == cli.EXIT_OK
    assert capsys.readouterr().out == "claude back to sonnet from its next turn.\n"
    assert not (relay / "model-overrides.json").exists()


def test_roles_model_keep_writes_config_toml(tmp_path: Path, capsys):
    relay = _sonnet_config(tmp_path)
    repo = ["--repo", str(tmp_path)]
    assert cli.main(
        ["roles", *repo, "model", "claude", "opus", "--keep"]
    ) == cli.EXIT_OK
    assert 'model = "opus"' in (relay / "config.toml").read_text()


@pytest.mark.parametrize(
    "extra", [["claude"], ["claude", "opus", "--reset"], ["claude", "--reset", "--keep"]]
)
def test_roles_model_needs_a_model_or_reset(tmp_path: Path, capsys, extra):
    _sonnet_config(tmp_path)
    code = cli.main(["roles", "--repo", str(tmp_path), "model", *extra])
    assert code == cli.EXIT_USAGE
    assert "--reset" in capsys.readouterr().err


def test_roles_model_refuses_an_agent_without_a_model_flag(tmp_path: Path, capsys):
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        '[agents.tool]\nadapter = "generic"\ncommand = ["tool"]\n'
    )
    code = cli.main(["roles", "--repo", str(tmp_path), "model", "tool", "big"])
    assert code == cli.EXIT_USAGE
    assert capsys.readouterr().err == (
        "Error: tool has no way to set a model; set model_flag under [agents.tool]\n"
    )
