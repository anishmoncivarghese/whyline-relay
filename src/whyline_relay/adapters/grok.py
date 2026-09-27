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
