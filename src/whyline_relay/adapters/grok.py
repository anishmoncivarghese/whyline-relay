"""Structured-output helpers for Grok used through the generic adapter.

Grok remains generic: the relay cannot check its login and does not manage its
permission policy.  Its executable and JSON envelope are nevertheless known
well enough to avoid treating echoed reasoning as a machine-readable failure.
"""

from __future__ import annotations

from pathlib import Path

from whyline_relay.adapters.base import Adapter, json_object


def payload(adapter: Adapter, text: str, command: list[str]) -> dict | None:
    if (
        adapter.name != "generic"
        or not command
        or Path(command[0]).name != "grok"
    ):
        return None
    return json_object(text)


def cancelled(adapter: Adapter, text: str, command: list[str]) -> bool:
    result = payload(adapter, text, command)
    return result is not None and result.get("stopReason") == "cancelled"


def rate_limit_text(adapter: Adapter, text: str, command: list[str]) -> str:
    """Text safe to inspect for a real Grok rate-limit signal.

    A cancelled result is ambiguous (the CLI uses it for denied commands) and
    must never route from prose in ``thought``.  A completed result can still
    report a genuine quota error in its final user-facing fields.
    """
    result = payload(adapter, text, command)
    if result is None:
        return text
    if result.get("stopReason") == "cancelled":
        return ""
    fields = (result.get(name) for name in ("text", "message", "error"))
    return "\n".join(value for value in fields if isinstance(value, str))


def cancellation_detail(adapter: Adapter, text: str, command: list[str]) -> str:
    if not cancelled(adapter, text, command):
        return ""
    return (
        '; Grok reported stopReason "cancelled"; its headless CLI uses this '
        "for permission policy cancellations without naming the denied command. "
        "Check the configured --allow rules"
    )


# How many times a cancelled Grok turn is resumed before it counts as failed.
RESUMES = 2


def resume_command(command: list[str], session_id: str) -> list[str] | None:
    """The same command continuing ``session_id``, or None if it cannot.

    The relay appends the prompt after the final ``-p``, so ``--resume`` goes
    just before it.
    """
    if not command or command[-1] != "-p":
        return None
    return [*command[:-1], "--resume", session_id, "-p"]


def resumable_session(adapter: Adapter, text: str, command: list[str]) -> str | None:
    """The session id of a cancelled Grok turn the relay can resume, else None.

    Headless Grok ends the whole turn as "cancelled" when a command falls
    outside its allow list (measured on grok 1.0.41, even for a plain ``cp``),
    rather than refusing that one command.  Resuming the same session keeps
    everything it already did.
    """
    if not cancelled(adapter, text, command) or resume_command(command, "x") is None:
        return None
    session = payload(adapter, text, command).get("sessionId")
    return session if isinstance(session, str) and session else None


def resume_prompt(command: list[str]) -> str:
    allowed = [command[i + 1] for i, arg in enumerate(command[:-1]) if arg == "--allow"]
    listed = ", ".join(allowed) if allowed else "your read and edit tools"
    return (
        "Your previous turn was stopped because a shell command was not "
        "permitted in this headless run; nobody can approve a prompt. Continue "
        "the same task from where you stopped. Run one plain command per shell "
        "call: no if/for/while, subshells, $(...), backticks, background & or "
        f"bash -c. Allowed: {listed}, plus your read and edit tools. When the "
        "task is done, hand off exactly as the original instructions say."
    )
