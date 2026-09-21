"""Preflight checks shared by ``doctor``, ``start`` and ``resume``."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, TextIO

from whyline_relay import config, gitcheck, plan, running

Status = Literal["ok", "warn", "FAIL"]
Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Check:
    status: Status
    message: str
    hint: str | None = None


def _result(status: Status, message: str, hint: str | None = None) -> Check:
    return Check(status=status, message=message.replace("\n", " "), hint=hint)


def _inside_git(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _whyline(root: Path, runner: Runner) -> Check:
    try:
        result = runner(
            ["whyline", "sync"], cwd=str(root), capture_output=True, text=True
        )
    except FileNotFoundError:
        return _result(
            "FAIL",
            "whyline is not installed",
            "install whyline: uv tool install whyline",
        )
    except OSError as error:
        return _result(
            "FAIL",
            f"could not run whyline sync: {error}",
            "install whyline: uv tool install whyline",
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        message = "whyline is not initialised"
        if detail:
            message += f": {detail}"
        return _result("FAIL", message, "whyline init --yes")
    return _result("ok", "whyline is installed and initialised")


def _relay_setup(root: Path) -> tuple[Check, config.Config | None]:
    target = config.config_path(root)
    if not target.is_file():
        return (
            _result(
                "FAIL", f"relay config is missing: {target}", "whyline-relay init"
            ),
            None,
        )
    try:
        settings = config.load(root)
    except (config.ConfigError, TypeError, ValueError) as error:
        return (
            _result("FAIL", f"relay config is invalid: {error}", "whyline-relay init"),
            None,
        )

    missing: list[Path] = []
    invalid: list[str] = []
    for command in settings.agents.values():
        for index, argument in enumerate(command):
            if argument != "--settings":
                continue
            if index + 1 == len(command):
                invalid.append("--settings has no path")
                continue
            path = Path(command[index + 1])
            resolved = path if path.is_absolute() else root / path
            if not resolved.is_file():
                missing.append(path)
    if invalid or missing:
        details = [*invalid, *(f"settings file is missing: {path}" for path in missing)]
        return _result("FAIL", "; ".join(details), "whyline-relay init"), settings
    return _result("ok", "relay setup is complete"), settings


def _programs(settings: config.Config | None) -> tuple[list[Check], list[str]]:
    if settings is None:
        return [], []
    checks: list[Check] = []
    programs: list[str] = []
    for command in settings.agents.values():
        if not command:
            checks.append(
                _result("FAIL", "configured agent command is empty", "whyline-relay init")
            )
            continue
        program = command[0]
        if program in programs:
            continue
        programs.append(program)
        if shutil.which(program) is None:
            checks.append(
                _result(
                    "FAIL",
                    f"{program} is not on PATH",
                    f"install {program} and make sure it is on PATH",
                )
            )
        else:
            checks.append(_result("ok", f"{program} is on PATH"))
    return checks, programs


def _logins(root: Path, programs: list[str], runner: Runner) -> list[Check]:
    checks: list[Check] = []
    seen: set[str] = set()
    for configured_program in programs:
        program = Path(configured_program).name
        if program not in {"codex", "claude"} or program in seen:
            continue
        seen.add(program)
        argv = (
            ["codex", "login", "status"]
            if program == "codex"
            else ["claude", "auth", "status"]
        )
        fix = "codex login" if program == "codex" else "claude auth login"
        try:
            result = runner(
                argv,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            checks.append(
                _result(
                    "warn",
                    f"could not check {program} login: timed out after 20 seconds",
                    fix,
                )
            )
            continue
        except (FileNotFoundError, OSError) as error:
            checks.append(
                _result("warn", f"could not check {program} login: {error}", fix)
            )
            continue
        if result.returncode == 0:
            checks.append(_result("ok", f"{program} is logged in"))
        else:
            checks.append(_result("FAIL", f"{program} is not logged in", fix))
    return checks


def _plan_checks(plan_path: Path) -> list[Check]:
    if not plan_path.is_file():
        return [_result("FAIL", f"plan file does not exist: {plan_path}")]
    try:
        content = plan_path.read_text(encoding="utf-8")
        tasks = plan.parse(content)
    except (OSError, UnicodeError, plan.PlanError) as error:
        return [_result("FAIL", str(error))]

    checks: list[Check] = []
    if plan.next_unchecked(tasks) is None:
        checks.append(_result("FAIL", f"no unchecked tasks in {plan_path}"))
    else:
        checks.append(_result("ok", f"plan parses and has unchecked tasks: {plan_path}"))
    for task in tasks:
        title = task.text.splitlines()[0]
        if ":" not in title:
            checks.append(
                _result(
                    "warn",
                    f"task {task.task_id!r} has no ID before a colon; its id is its first word",
                )
            )
        if len(task.text.splitlines()) == 1:
            checks.append(_result("warn", f"task {task.task_id!r} has no detail lines"))
    return checks


def run(
    root: Path,
    plan_path: Path | None = None,
    *,
    allow_dirty: bool = False,
    runner: Runner = subprocess.run,
) -> list[Check]:
    """Run every preflight check, returning results in display order."""
    root = root.resolve()
    results = [
        _result("ok", "directory is inside a git repository")
        if _inside_git(root)
        else _result(
            "FAIL",
            "directory is not inside a git repository",
            "run it inside a git repository",
        )
    ]
    results.append(_whyline(root, runner))
    setup, settings = _relay_setup(root)
    results.append(setup)
    program_checks, programs = _programs(settings)
    results.extend(program_checks)
    results.extend(_logins(root, programs, runner))

    selected_plan = plan_path
    if selected_plan is None:
        selected_plan = root / (
            settings.plan if settings is not None else config.DEFAULTS["plan"]
        )
    elif not selected_plan.is_absolute():
        selected_plan = root / selected_plan
    results.extend(_plan_checks(selected_plan))

    if allow_dirty:
        results.append(_result("ok", "working-tree check skipped (--allow-dirty)"))
    else:
        try:
            dirty = gitcheck.is_dirty(root)
        except gitcheck.GitError as error:
            results.append(_result("FAIL", f"could not check working tree: {error}"))
        else:
            results.append(
                _result(
                    "FAIL",
                    "working tree has uncommitted changes",
                    "commit or stash your changes, or pass --allow-dirty",
                )
                if dirty
                else _result("ok", "working tree is clean")
            )

    active = running.live(root)
    results.append(
        _result(
            "FAIL",
            f"another relay is running here (pid {active.pid})",
            "wait for it, or run: whyline-relay stop",
        )
        if active is not None
        else _result("ok", "no other relay is running here")
    )
    return results


def failures(checks: list[Check]) -> int:
    return sum(check.status == "FAIL" for check in checks)


def print_checks(
    checks: list[Check],
    *,
    stream: TextIO,
    include_ok: bool,
    summary: bool,
) -> None:
    visible = checks if include_ok else [check for check in checks if check.status != "ok"]
    for check in visible:
        print(f"  {check.status:<4}  {check.message}", file=stream)
        if check.status != "ok" and check.hint:
            print(f"        fix: {check.hint}", file=stream)
    if summary:
        count = failures(checks)
        print("All checks passed." if count == 0 else f"{count} problem(s) found.", file=stream)
