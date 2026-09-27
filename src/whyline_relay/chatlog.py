"""Shared, per-repo conversation history for `whyline-relay chat`.

One JSON object per line, oldest first, append-only. Never imports or reads
anything from whyline itself -- same rule `whyline_model.py` already follows,
so ownership of this file never contends with any other tool.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from math import ceil
from pathlib import Path

DEFAULT_TOKEN_BUDGET = 1200


def _path(root: Path) -> Path:
    return root / ".whyline" / "relay" / "chat-history.jsonl"


def _approximate_tokens(text: str) -> int:
    """Conservative, dependency-free estimate: bytes over three, not a real
    tokeniser. Every caller must treat this as approximate."""
    return ceil(len(text.encode("utf-8")) / 3)


def append(
    root: Path,
    *,
    agent: str,
    prompt: str,
    response: str,
    files_changed: int,
    ok: bool,
) -> dict:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "agent": agent,
        "prompt": prompt,
        "response": response,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "files_changed": files_changed,
        "ok": ok,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return record


def load(root: Path) -> list[dict]:
    path = _path(root)
    if not path.exists():
        return []
    turns: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            turns.append(json.loads(line))
        except ValueError:
            continue
    return turns


def _format_turn(turn: dict) -> str:
    return f"[{turn['agent']}] {turn['prompt']}\n{turn['response']}"


def recent(root: Path, token_budget: int = DEFAULT_TOKEN_BUDGET) -> str:
    turns = load(root)
    if not turns:
        return ""
    kept: list[str] = []
    used = 0
    for turn in reversed(turns):
        formatted = _format_turn(turn)
        cost = _approximate_tokens(formatted)
        if kept and used + cost > token_budget:
            break
        kept.append(formatted)
        used += cost
    return "\n\n".join(reversed(kept))


def clear(root: Path) -> None:
    """Delete this repo's chat history, if any. Safe to call when absent."""
    path = _path(root)
    if path.exists():
        path.unlink()
