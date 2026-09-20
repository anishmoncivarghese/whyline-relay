"""Shared test setup."""

import types

import pytest

from whyline_relay import cli


@pytest.fixture(autouse=True)
def no_desktop_notifications(monkeypatch):
    """The CLI announces pauses and completions; a test run must never pop a real one."""
    monkeypatch.setattr(
        cli, "notify", types.SimpleNamespace(send=lambda *args, **kwargs: None)
    )
