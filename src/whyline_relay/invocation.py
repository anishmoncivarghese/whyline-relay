"""The command name used in user-facing guidance."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_DEFAULT_PROG = __package__.split(".", 1)[0].replace("_", "-")
_PROG: ContextVar[str] = ContextVar("whyline_relay_prog", default=_DEFAULT_PROG)


def prog() -> str:
    """Return the command name for the current invocation."""
    return _PROG.get()


def command(name: str) -> str:
    """Return a subcommand spelled as the user can invoke it."""
    return f"{prog()} {name}"


@contextmanager
def called_as(name: str) -> Iterator[None]:
    """Use ``name`` in guidance for the duration of one embedded call."""
    token = _PROG.set(name)
    try:
        yield
    finally:
        _PROG.reset(token)
