"""Where the relay is, so `resume` can pick it up."""

from __future__ import annotations

import errno
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from whyline_relay import config


class DiskFull(Exception):
    """A state write failed because the volume is out of space.

    The previous state file is left as it was. `path` is that file, and
    `free_bytes` is what the volume still reported afterwards.
    """

    def __init__(self, path: Path, free_bytes: int):
        self.path = path
        self.free_bytes = free_bytes
        super().__init__(path, free_bytes)


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def _write_atomic(target: Path, payload: str) -> None:
    """Write `payload` to `target` via a temp file in the same directory.

    Both the write and the replace sit in one try. ENOSPC or EDQUOT removes
    the temp file, leaves `target` untouched, and raises DiskFull.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, target)
    except OSError as error:
        if getattr(error, "errno", None) not in (errno.ENOSPC, errno.EDQUOT):
            raise
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise DiskFull(target, _free_bytes(target.parent)) from error


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
    # None means this record predates the field. Resume then uses config.load.
    # A stored 0 or any stored positive int is the cap for this run.
    timeout_minutes: int | None = None


@dataclass(frozen=True)
class PlanState:
    description: str
    stage: str
    round: int
    stage_visits: dict[str, int]
    agent: str
    feedback: str
    draft_path: str
    paused_reason: str
    log_path: str
    attachments: list[str] = field(default_factory=list)
    # None means this record predates the field. Resume then uses config.load.
    # A stored 0 or any stored positive int is the cap for this run.
    timeout_minutes: int | None = None


def path(root: Path) -> Path:
    return config.relay_dir(root) / "state.json"


def save(root: Path, value: RelayState) -> None:
    """Write atomically: a half-written state file would strand a resume."""
    _write_atomic(path(root), json.dumps(asdict(value), indent=2))


def load(root: Path) -> RelayState | None:
    try:
        record = json.loads(path(root).read_text(encoding="utf-8"))
        return RelayState(**record)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def clear(root: Path) -> None:
    path(root).unlink(missing_ok=True)


def plan_path(root: Path, name: str = "plan-state.json") -> Path:
    return config.relay_dir(root) / name


def save_plan(root: Path, value: PlanState, name: str = "plan-state.json") -> None:
    """Write atomically, exactly like save() -- a half-written checkpoint would
    strand a `resume` the same way for either kind of state."""
    _write_atomic(plan_path(root, name=name), json.dumps(asdict(value), indent=2))


def load_plan(root: Path, name: str = "plan-state.json") -> PlanState | None:
    try:
        record = json.loads(plan_path(root, name=name).read_text(encoding="utf-8"))
        return PlanState(**record)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def clear_plan(root: Path, name: str = "plan-state.json") -> None:
    plan_path(root, name=name).unlink(missing_ok=True)
