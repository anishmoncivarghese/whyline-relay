"""Flags that switch an agent's permission checks off. The relay refuses them; it never passes them.
This module is the single place that names them, and the guard test exempts it by name.
"""

from __future__ import annotations

from collections.abc import Sequence

FLAGS = {
    "codex": (
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
    ),
    "claude": ("--dangerously-skip-permissions",),
}
SANDBOX_OFF = "danger-full-access"
MODE_OFF = "bypassPermissions"


def find(adapter_name: str, command: Sequence[str]) -> list[str]:
    """The refused flags present in `command`, in order; empty for a generic agent (not inspectable)."""
    if adapter_name not in FLAGS:
        return []

    found: list[str] = []
    for index, argument in enumerate(command):
        if any(
            argument == flag or argument.startswith(f"{flag}=")
            for flag in FLAGS[adapter_name]
        ):
            found.append(argument)

        following = command[index + 1] if index + 1 < len(command) else None
        if adapter_name == "codex":
            if argument in ("-s", "--sandbox") and following == SANDBOX_OFF:
                found.append(f"{argument} {following}")
            elif argument == f"--sandbox={SANDBOX_OFF}":
                found.append(argument)
        elif adapter_name == "claude":
            if argument == "--permission-mode" and following == MODE_OFF:
                found.append(f"{argument} {following}")
            elif argument == f"--permission-mode={MODE_OFF}":
                found.append(argument)
    return found
