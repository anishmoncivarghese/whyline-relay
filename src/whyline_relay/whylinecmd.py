"""The whyline commands the relay issues on its own behalf."""

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


# Whether `whyline handoff` accepts `--issue`; None until first asked.
_issue_support: bool | None = None


def supports_issues(root: Path, runner=None) -> bool:
    """True when this whyline's `handoff` takes `--issue` (agentdock RST-5).

    An older whyline refuses the flag, so prompts only ask for it when
    `whyline handoff --help` lists it. Asked once per process: the installed
    whyline does not change under a running relay.
    """
    global _issue_support
    if _issue_support is None:
        try:
            result = _run(runner, ["whyline", "handoff", "--help"], root)
        except WhylineUnavailable:
            _issue_support = False
        else:
            _issue_support = result.returncode == 0 and "--issue" in (
                result.stdout or ""
            )
    return _issue_support


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


def note(
    root: Path,
    text: str,
    *,
    because: str,
    task: str,
    actor: str,
    role: str,
    runner=None,
) -> None:
    """Record a decision with `whyline note`.

    Raises WhylineUnavailable when whyline is missing or refuses. Callers that
    must keep going, such as `resume --retry-stage`, catch that and warn.
    """
    result = _run(
        runner,
        [
            "whyline",
            "note",
            text,
            "--because",
            because,
            "--actor",
            actor,
            "--role",
            role,
            "--task",
            task,
        ],
        root,
    )
    if result.returncode != 0:
        raise WhylineUnavailable(
            f"whyline note failed ({result.returncode}): {(result.stderr or '').strip()}"
        )
