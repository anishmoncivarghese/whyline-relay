"""Best-effort desktop notification. No network, no service, never fatal."""

from __future__ import annotations

import subprocess
import sys


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def command(title: str, message: str, platform: str) -> list[str] | None:
    """The argv for this platform's notifier, or None if we do not know one."""
    if platform == "darwin":
        script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
        return ["osascript", "-e", script]
    if platform.startswith("linux"):
        return ["notify-send", title, message]
    return None


def send(title: str, message: str, runner=None, platform: str | None = None) -> None:
    """Notify if we can. A missing notifier is not an error worth surfacing."""
    argv = command(title, message, platform or sys.platform)
    if argv is None:
        return
    run = runner if runner is not None else subprocess.run
    try:
        run(argv, capture_output=True)
    except (OSError, ValueError):
        return
