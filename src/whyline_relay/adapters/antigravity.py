"""Structured-output helpers for Antigravity used through the generic adapter.

Antigravity remains generic: the relay cannot check its login. A refused tool
call still exits 0 with ``"status": "SUCCESS"`` (measured on agy 1.4.12) and
shows only in the JSON's ``denied_actions``, which names the tool type but
never the command, so the relay can report a refusal but not what was refused.
"""

from __future__ import annotations

from pathlib import Path

from whyline_relay.adapters.base import Adapter, json_object


def payload(adapter: Adapter, text: str, command: list[str]) -> dict | None:
    if (
        adapter.name != "generic"
        or not command
        or Path(command[0]).name != "agy"
    ):
        return None
    return json_object(text)


def denied_detail(adapter: Adapter, text: str, command: list[str]) -> str:
    """What agy refused, for the human only; "" when it refused nothing."""
    result = payload(adapter, text, command)
    denied = result.get("denied_actions") if result is not None else None
    if not isinstance(denied, list) or not denied:
        return ""
    names = [
        str(action.get("display_name") or action.get("action") or "unknown")
        if isinstance(action, dict)
        else "unknown"
        for action in denied
    ]
    return (
        f"antigravity refused {len(denied)} tool call(s) ({', '.join(names)}). "
        "agy names only the tool type, not the command; add what it needs to "
        "the --settings allow list in [agents.antigravity], or run it yourself."
    )
