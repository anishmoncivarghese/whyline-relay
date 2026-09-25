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

# A generic agent's own adapter_name is always "generic" -- FLAGS has no entry
# to look up, and an arbitrary generic command has no known flag vocabulary at
# all. But some binaries reachable only through the generic adapter today
# (Antigravity, and any future tool documented the same way before it earns a
# real adapter) DO have a known bypass flag, keyed here by the binary itself
# (command[0]), independent of adapter_name. This runs in addition to, never
# instead of, the adapter_name-based check above, so a managed adapter's own
# custom-command alias (whose command[0] need not literally be "codex"/
# "claude") keeps being checked purely by adapter_name as before.
SIMPLE_BINARY_FLAGS = {
    "agy": ("--dangerously-skip-permissions",),
}
MODE_BINARY_FLAGS = {
    "grok": ("--permission-mode", "bypassPermissions"),
}


def find(adapter_name: str, command: Sequence[str]) -> list[str]:
    """The refused flags present in `command`, in order.

    Empty for a generic agent whose binary (command[0]) isn't one of the known
    exceptions above -- an arbitrary generic command has no flag vocabulary
    we can check at all.
    """
    found: list[str] = []
    binary = command[0] if command else None

    if adapter_name in FLAGS:
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

    if binary in SIMPLE_BINARY_FLAGS:
        for argument in command:
            if any(
                argument == flag or argument.startswith(f"{flag}=")
                for flag in SIMPLE_BINARY_FLAGS[binary]
            ):
                found.append(argument)

    if binary in MODE_BINARY_FLAGS:
        mode_flag, mode_off = MODE_BINARY_FLAGS[binary]
        for index, argument in enumerate(command):
            following = command[index + 1] if index + 1 < len(command) else None
            if argument == mode_flag and following == mode_off:
                found.append(f"{argument} {following}")
            elif argument == f"{mode_flag}={mode_off}":
                found.append(argument)

    return found
