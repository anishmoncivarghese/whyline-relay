"""Reading whyline's active handoff record. The relay reads it; it never writes it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Handoff:
    event_id: str
    task: str
    to_actor: str
    status: str
    summary: str
    questions: tuple[str, ...] = ()


def path(root: Path) -> Path:
    return root / ".whyline" / "active-handoff.json"


def _text(record: dict, key: str) -> str:
    value = record.get(key)
    return value if isinstance(value, str) else ""


def _texts(record: dict, key: str) -> tuple[str, ...]:
    value = record.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def read(root: Path) -> Handoff | None:
    """Return the current handoff, or None if it is absent or unreadable.

    An unreadable record is treated as absent on purpose: the caller's next move
    is to pause either way, and guessing at half-written JSON is how a relay
    routes on a lie.
    """
    try:
        record = json.loads(path(root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    return Handoff(
        event_id=_text(record, "id"),
        task=_text(record, "task"),
        to_actor=_text(record, "to_actor"),
        status=_text(record, "status"),
        summary=_text(record, "summary"),
        questions=_texts(record, "questions"),
    )
