"""The two whyline commands the relay issues on its own behalf."""

from __future__ import annotations

import subprocess
from pathlib import Path


class WhylineUnavailable(RuntimeError):
    """whyline is not installed, or refused the command."""


def _run(runner, argv: list[str], root: Path) -> subprocess.CompletedProcess:
    run = runner if runner is not None else subprocess.run
    try:
        return run(argv, cwd=str(root), capture_output=True, text=True)
    except FileNotFoundError as error:
        raise WhylineUnavailable("whyline is not installed or not on PATH") from error


def sync(root: Path, task: str, runner=None) -> str:
    """Return the compact active-task packet for `task`."""
    result = _run(runner, ["whyline", "sync", "--task", task], root)
    if result.returncode != 0:
        raise WhylineUnavailable(
            f"whyline sync failed ({result.returncode}): {(result.stderr or '').strip()}"
        )
    return result.stdout


def claim(root: Path, task: str, actor: str, role: str, runner=None) -> None:
    """Record advisory ownership. A conflict is a warning from whyline, not an error."""
    result = _run(
        runner,
        ["whyline", "claim", task, "--actor", actor, "--role", role],
        root,
    )
    if result.returncode != 0:
        raise WhylineUnavailable(
            f"whyline claim failed ({result.returncode}): {(result.stderr or '').strip()}"
        )
