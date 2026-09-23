"""Where the relay is, so `resume` can pick it up."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from whyline_relay import config


@dataclass(frozen=True)
class RelayState:
    plan: str
    branch: str
    task_id: str
    round: int
    base_commit: str
    paused_reason: str
    log_path: str
    only: str = ""
    last_handoff_id: str = ""
    # Configured-pipeline resume only (empty/absent for a legacy, unconfigured run).
    profile: str = ""
    stage: str = ""
    stage_visits: dict[str, int] = field(default_factory=dict)
    pipeline_fingerprint: str = ""


def path(root: Path) -> Path:
    return config.relay_dir(root) / "state.json"


def save(root: Path, value: RelayState) -> None:
    """Write atomically: a half-written state file would strand a resume."""
    target = path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(value), indent=2), encoding="utf-8")
    os.replace(temporary, target)


def load(root: Path) -> RelayState | None:
    try:
        record = json.loads(path(root).read_text(encoding="utf-8"))
        return RelayState(**record)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def clear(root: Path) -> None:
    path(root).unlink(missing_ok=True)
