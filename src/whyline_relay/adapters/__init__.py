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
