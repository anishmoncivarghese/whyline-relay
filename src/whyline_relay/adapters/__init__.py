"""Registry of relay agent adapters."""

from __future__ import annotations

from whyline_relay.adapters.base import Adapter
from whyline_relay.adapters.claude import ADAPTER as CLAUDE
from whyline_relay.adapters.codex import ADAPTER as CODEX
from whyline_relay.adapters.generic import ADAPTER as GENERIC

BUILTIN: dict[str, Adapter] = {"codex": CODEX, "claude": CLAUDE}


def get(name: str) -> Adapter:
    if name == "generic":
        return GENERIC
    try:
        return BUILTIN[name]
    except KeyError:
        raise KeyError(name) from None


def describe(adapter: Adapter, role: str, agent: str) -> str:
    """Summarise the safeguards an adapter manages for one role."""
    manages = adapter.manages
    permissions = "managed" if manages.permissions else "not managed"
    login = "checked" if manages.login else "not verified"
    denials = "reported" if manages.denials else "not reported"
    return (
        f"{role}: {agent}  permissions: {permissions}  login: {login}  "
        f"denials: {denials}"
    )
