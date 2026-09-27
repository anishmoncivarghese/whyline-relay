import json
from pathlib import Path

from whyline_relay import chatlog


def test_append_creates_the_file_and_writes_one_json_line(tmp_path: Path):
    chatlog.append(
        tmp_path, agent="claude", prompt="hi", response="hello",
        files_changed=0, ok=True,
    )
    path = tmp_path / ".whyline" / "relay" / "chat-history.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["agent"] == "claude"
    assert record["prompt"] == "hi"
    assert record["response"] == "hello"
    assert record["files_changed"] == 0
    assert record["ok"] is True
    assert "timestamp" in record


def test_load_returns_no_turns_when_the_file_is_absent(tmp_path: Path):
    assert chatlog.load(tmp_path) == []


def test_load_skips_a_corrupted_line_without_crashing(tmp_path: Path):
    chatlog.append(
        tmp_path, agent="codex", prompt="a", response="b",
        files_changed=1, ok=True,
    )
    path = tmp_path / ".whyline" / "relay" / "chat-history.jsonl"
    with path.open("a") as handle:
        handle.write("not json\n")
    chatlog.append(
        tmp_path, agent="codex", prompt="c", response="d",
        files_changed=0, ok=True,
    )
    turns = chatlog.load(tmp_path)
    assert [t["prompt"] for t in turns] == ["a", "c"]


def test_recent_returns_empty_string_with_no_history(tmp_path: Path):
    assert chatlog.recent(tmp_path) == ""


def test_recent_includes_every_turn_within_budget(tmp_path: Path):
    chatlog.append(
        tmp_path, agent="claude", prompt="q1", response="a1",
        files_changed=0, ok=True,
    )
    chatlog.append(
        tmp_path, agent="codex", prompt="q2", response="a2",
        files_changed=0, ok=True,
    )
    text = chatlog.recent(tmp_path, token_budget=1200)
    assert "q1" in text
    assert "a1" in text
    assert "q2" in text
    assert "a2" in text
    # oldest first, matching a normal transcript's reading order
    assert text.index("q1") < text.index("q2")


def test_recent_drops_the_oldest_turns_first_once_over_budget(tmp_path: Path):
    for i in range(50):
        chatlog.append(
            tmp_path, agent="claude", prompt=f"question {i}" * 20,
            response=f"answer {i}" * 20, files_changed=0, ok=True,
        )
    text = chatlog.recent(tmp_path, token_budget=200)
    assert "question 49" in text
    assert "question 0" * 20 not in text


def test_clear_removes_the_history_file(tmp_path: Path):
    chatlog.append(
        tmp_path, agent="claude", prompt="q", response="a",
        files_changed=0, ok=True,
    )
    chatlog.clear(tmp_path)
    assert chatlog.load(tmp_path) == []
    assert not (tmp_path / ".whyline" / "relay" / "chat-history.jsonl").exists()


def test_clear_is_a_no_op_when_there_is_no_history(tmp_path: Path):
    chatlog.clear(tmp_path)  # must not raise
    assert chatlog.load(tmp_path) == []
