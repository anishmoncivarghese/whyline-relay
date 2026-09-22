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
