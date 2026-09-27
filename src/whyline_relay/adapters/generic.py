"""Generic adapter for explicitly configured agent commands."""

from __future__ import annotations

from whyline_relay.adapters.base import Adapter, Manages, extract_or_fallback, last_line_detail

GENERIC_RESPONSE_FIELDS = ("result", "response", "text", "message")


def extract_response(text: str) -> str:
    return extract_or_fallback(text, GENERIC_RESPONSE_FIELDS)


ADAPTER = Adapter(
    name="generic",
    default_command=None,
    binary=None,
    login_argv=None,
    login_fix=None,
    permission_files=lambda stack: {},
    diagnose=last_line_detail,
    manages=Manages(False, False, False),
    model_flag=None,
    extract_response=extract_response,
    uses_output_file=False,
)
