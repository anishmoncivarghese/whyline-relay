"""The live-run marker used to keep one relay process in a repository."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from whyline_relay import config


@dataclass(frozen=True)
class Running:
    agent: str
    task: str
    round: int
    started: str
    pid: int


class AlreadyRunning(RuntimeError):
    """Another live relay owns this repository."""

    def __init__(self, pid: int):
        super().__init__(f"another relay is running here (pid {pid})")
        self.pid = pid


def path(root: Path) -> Path:
    return config.relay_dir(root) / "running.json"


def read(root: Path) -> Running | None:
    try:
        record = json.loads(path(root).read_text(encoding="utf-8"))
        marker = Running(**record)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if marker.agent not in ("codex", "claude"):
        return None
    if not isinstance(marker.task, str) or not isinstance(marker.round, int):
        return None
    if not isinstance(marker.started, str) or not isinstance(marker.pid, int):
        return None
    return marker


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def live(root: Path) -> Running | None:
    marker = read(root)
    return marker if marker is not None and pid_is_alive(marker.pid) else None


def _temporary(target: Path, marker: Running) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=".running-",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(asdict(marker), handle, indent=2)
            handle.write("\n")
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def start_turn(root: Path, agent: str, task: str, round_: int) -> Running:
    """Atomically claim the repository, or update this process's current turn."""
    marker = Running(
        agent=agent,
        task=task,
        round=round_,
        started=datetime.now().astimezone().isoformat(),
        pid=os.getpid(),
    )
    target = path(root)
    temporary = _temporary(target, marker)
    try:
        while True:
            current = read(root)
            if current is not None and pid_is_alive(current.pid):
                if current.pid != marker.pid:
                    raise AlreadyRunning(current.pid)
                os.replace(temporary, target)
                return marker
            if target.exists():
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
                continue
            try:
                # Linking a fully written temporary file makes the initial claim
                # visible atomically, so two relays cannot both win a race here.
                os.link(temporary, target)
            except FileExistsError:
                continue
            temporary.unlink()
            return marker
    finally:
        temporary.unlink(missing_ok=True)


def clear(root: Path) -> None:
    """Remove this process's marker without disturbing a newer relay owner."""
    marker = read(root)
    if marker is not None and marker.pid == os.getpid():
        path(root).unlink(missing_ok=True)
