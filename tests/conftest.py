"""Shared test setup."""

import types

import pytest

from whyline_relay import cli, preflight


@pytest.fixture(autouse=True)
def no_desktop_notifications(monkeypatch):
    """The CLI announces pauses and completions; a test run must never pop a real one."""
    monkeypatch.setattr(
        cli, "notify", types.SimpleNamespace(send=lambda *args, **kwargs: None)
    )
    # Existing command tests exercise behavior after startup. Keep them hermetic:
    # focused preflight tests install their own results or call the module directly.
    monkeypatch.setattr(
        cli,
        "_launch_checks",
        lambda root, plan_path, *, allow_dirty: [
            preflight.Check("ok", "test preflight")
        ],
    )
