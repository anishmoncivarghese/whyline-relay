import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from whyline_relay import running


def write_marker(root: Path, *, agent: str, pid: int, **extra: object) -> Path:
    target = root / ".whyline" / "relay" / "running.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "agent": agent,
                "task": "T-1",
                "round": 1,
                "started": datetime.now().astimezone().isoformat(),
                "pid": pid,
                **extra,
            }
        ),
        encoding="utf-8",
    )
    return target


def test_a_live_marker_for_any_agent_name_blocks_a_second_relay(tmp_path):
    write_marker(tmp_path, agent="gemini", pid=os.getppid())
    assert running.live(tmp_path).agent == "gemini"
    with pytest.raises(running.AlreadyRunning):
        running.start_turn(tmp_path, "aider", "T-1", 1)


def test_an_empty_agent_name_is_rejected(tmp_path):
    write_marker(tmp_path, agent="", pid=os.getpid())
    assert running.read(tmp_path) is None


def test_an_old_marker_without_a_role_loads_with_an_empty_role(tmp_path):
    write_marker(tmp_path, agent="claude", pid=os.getpid())
    assert running.read(tmp_path).role == ""


def test_a_non_string_role_is_rejected(tmp_path):
    write_marker(tmp_path, agent="gemini", pid=os.getpid(), role=42)
    assert running.read(tmp_path) is None


def test_start_turn_writes_the_role(tmp_path):
    marker = running.start_turn(tmp_path, "gemini", "T-1", 1, role="reviewer")
    record = json.loads(running.path(tmp_path).read_text(encoding="utf-8"))

    assert marker.role == "reviewer"
    assert record["role"] == "reviewer"

