"""The CLI can be embedded under a caller-provided command name."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from whyline_relay import cli, loop, preflight, running, state

EMBEDDED_PROG = "whyline relay"
COMMAND = re.compile(
    r"whyline-relay (?:start|resume|status|stop|init|remove|doctor)\b"
)


def _saved_pause(root: Path) -> None:
    state.save(
        root,
        state.RelayState(
            plan="plan.md",
            branch="relay/plan",
            task_id="APP-1",
            round=1,
            base_commit="abc",
            paused_reason="needs attention",
            log_path="/tmp/relay.log",
        ),
    )


def test_embedded_help_uses_prog_and_default_is_restored(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--help"], prog=EMBEDDED_PROG)
    assert stopped.value.code == 0
    assert capsys.readouterr().out.startswith("usage: whyline relay")

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert capsys.readouterr().out.startswith("usage: whyline-relay")


def test_explicit_argv_does_not_read_sys_argv(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["host-program", "unknown-command"])
    monkeypatch.setattr(
        sys,
        "exit",
        lambda code=0: pytest.fail(f"sys.exit called with {code}"),
    )

    assert (
        cli.main(["status", "--repo", str(tmp_path)], prog=EMBEDDED_PROG)
        == cli.EXIT_OK
    )
    assert capsys.readouterr().out == "No relay run in progress.\n"


def test_pause_status_and_interrupt_guidance_use_prog(
    tmp_path: Path, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli,
        "cmd_start",
        lambda args: cli._report_pause(loop.Paused("agent stopped", "/tmp/a.log")),
    )
    assert cli.main(["start"], prog=EMBEDDED_PROG) == cli.EXIT_PAUSED
    pause = capsys.readouterr().err
    assert "Resume with: whyline relay resume" in pause

    _saved_pause(tmp_path)
    assert (
        cli.main(["status", "--repo", str(tmp_path)], prog=EMBEDDED_PROG)
        == cli.EXIT_OK
    )
    status = capsys.readouterr().out
    assert "Resume with: whyline relay resume" in status

    monkeypatch.setattr(
        cli,
        "cmd_status",
        lambda args: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    assert cli.main(["status"], prog=EMBEDDED_PROG) == cli.EXIT_PAUSED
    interrupted = capsys.readouterr().err
    assert "Interrupted. Resume with: whyline relay resume" in interrupted

    assert not COMMAND.search(pause + status + interrupted)


@pytest.mark.parametrize("command", ["start", "resume", "remove"])
def test_live_run_refusals_use_prog(
    tmp_path: Path, command: str, monkeypatch, capsys
):
    marker = running.Running("codex", "APP-1", 1, "now", os.getpid())
    monkeypatch.setattr(cli.running, "live", lambda root: marker)
    argv = [command, "--repo", str(tmp_path)]
    if command == "remove":
        argv.append("--yes")

    assert cli.main(argv, prog=EMBEDDED_PROG) == cli.EXIT_ERROR

    error = capsys.readouterr().err
    assert "whyline relay stop" in error
    assert not COMMAND.search(error)


def test_paused_remove_refusal_uses_prog(tmp_path: Path, monkeypatch, capsys):
    relay = tmp_path / ".whyline" / "relay"
    relay.mkdir(parents=True)
    (relay / "state.json").write_text("{}")
    monkeypatch.setattr(cli.remove.gitcheck, "relay_ignore_count", lambda root: 0)

    assert (
        cli.main(
            ["remove", "--repo", str(tmp_path), "--yes"], prog=EMBEDDED_PROG
        )
        == cli.EXIT_ERROR
    )

    error = capsys.readouterr().err
    assert "whyline relay resume" in error
    assert not COMMAND.search(error)


def test_preflight_hints_and_init_next_step_use_prog(
    tmp_path: Path, monkeypatch, capsys
):
    marker = running.Running("codex", "APP-1", 1, "now", os.getpid())
    monkeypatch.setattr(preflight, "_inside_git", lambda root: True)
    monkeypatch.setattr(
        preflight,
        "_whyline",
        lambda root, runner: preflight.Check("ok", "whyline is ready"),
    )
    monkeypatch.setattr(preflight.gitcheck, "is_dirty", lambda root: False)
    monkeypatch.setattr(preflight.running, "live", lambda root: marker)

    assert (
        cli.main(
            ["doctor", "--repo", str(tmp_path), "--allow-dirty"],
            prog=EMBEDDED_PROG,
        )
        == cli.EXIT_ERROR
    )
    hints = capsys.readouterr().out
    assert "fix: whyline relay init" in hints
    assert "fix: wait for it, or run: whyline relay stop" in hints

    monkeypatch.setattr(preflight.running, "live", lambda root: None)
    assert (
        cli.main(
            ["init", "--repo", str(tmp_path), "--yes"], prog=EMBEDDED_PROG
        )
        == cli.EXIT_OK
    )
    initialized = capsys.readouterr().out
    assert "files before `whyline relay start`" in initialized
    assert not COMMAND.search(hints + initialized)


def test_plan_format_guidance_uses_prog(capsys):
    assert cli.main(["plan-format"], prog=EMBEDDED_PROG) == cli.EXIT_OK
    output = capsys.readouterr().out
    assert "`whyline relay start --plan PATH`" in output
    assert not COMMAND.search(output)


def test_source_has_no_hard_coded_relay_commands():
    source = Path(cli.__file__).parent
    offenders = [
        str(path.relative_to(source.parent.parent))
        for path in source.rglob("*.py")
        if COMMAND.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
