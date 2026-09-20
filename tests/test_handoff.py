import json
from pathlib import Path

from whyline_relay import handoff


def write_handoff(root: Path, **fields) -> None:
    target = root / ".whyline"
    target.mkdir(parents=True, exist_ok=True)
    record = {
        "v": 1,
        "id": "abc123",
        "ts": "2026-09-20T10:00:00.000Z",
        "type": "Handoff",
        "task": "WL-1",
        "from_actor": "codex",
        "to_actor": "claude",
        "status": "ready-for-review",
        "summary": "Implemented the cache",
        **fields,
    }
    (target / "active-handoff.json").write_text(json.dumps(record))


def test_read_returns_none_when_absent(tmp_path: Path):
    assert handoff.read(tmp_path) is None


def test_read_extracts_the_routing_fields(tmp_path: Path):
    write_handoff(tmp_path)
    record = handoff.read(tmp_path)
    assert record.event_id == "abc123"
    assert record.task == "WL-1"
    assert record.to_actor == "claude"
    assert record.status == "ready-for-review"
    assert record.summary == "Implemented the cache"


def test_read_returns_none_on_corrupt_json(tmp_path: Path):
    target = tmp_path / ".whyline"
    target.mkdir(parents=True)
    (target / "active-handoff.json").write_text("{not json")
    assert handoff.read(tmp_path) is None


def test_missing_fields_become_empty_strings(tmp_path: Path):
    write_handoff(tmp_path, summary=None)
    assert handoff.read(tmp_path).summary == ""
