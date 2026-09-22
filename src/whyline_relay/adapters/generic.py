"""Generic adapter for explicitly configured agent commands."""

from __future__ import annotations

from whyline_relay.adapters.base import Adapter, Manages, last_line_detail

ADAPTER = Adapter(
    name="generic",
    default_command=None,
    binary=None,
    login_argv=None,
    login_fix=None,
    permission_files=lambda stack: {},
    diagnose=last_line_detail,
    manages=Manages(False, False, False),
)
