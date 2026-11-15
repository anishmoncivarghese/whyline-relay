"""Preflight checks shared by ``doctor``, ``start`` and ``resume``."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, TextIO

from whyline_relay import (
    adapters,
    agents,
    config,
    failover,
    gitcheck,
    invocation,
    loop,
    pipeline as pipeline_module,
    plan,
    planner,
    prompts,
    recipes,
    roles,
    running,
)
from whyline_relay.adapters import bypass

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


def _agents_in_use(
    settings: config.Config,
) -> dict[str, tuple[list[str], str | None]]:
    agents: dict[str, tuple[list[str], str | None]] = {}
    if settings.pipeline is not None:
        for role in settings.pipeline.roles.values():
            if role.agent not in agents:
                agents[role.agent] = (settings.agents[role.agent], None)
    else:
        for role in ("implementer", "reviewer"):
            name = getattr(settings.roles, role)
            if name not in agents:
                agents[name] = (settings.agents[name], None)
    for name in settings.backup_chain:
        if name not in agents:
            agents[name] = (settings.agents[name], "the chain")
    return agents


def _relay_setup(root: Path) -> tuple[Check, config.Config | None]:
    target = config.config_path(root)
    if not target.is_file():
        return (
            _result(
                "FAIL", f"relay config is missing: {target}", invocation.command("init")
            ),
            None,
        )
    try:
        settings = config.load(root)
    except (config.ConfigError, TypeError, ValueError) as error:
        return (
            _result(
                "FAIL",
                f"relay config is invalid: {error}",
                invocation.command("init"),
            ),
            None,
        )

    problems: list[str] = []
    for _, (command, _) in _agents_in_use(settings).items():
        for value in recipes.settings_values(command):
            if not value:
                problems.append("--settings has no path")
                continue
            path = recipes.settings_file(value)
            if path is None:
                try:
                    json.loads(value)
                except ValueError:
                    problems.append(f"--settings is not valid JSON: {value[:60]}…")
                continue
            resolved = path if path.is_absolute() else root / path
            try:
                exists = resolved.is_file()
            except OSError:
                problems.append(f"--settings is not a usable path: {value[:60]}…")
                continue
            if not exists:
                problems.append(f"settings file is missing: {path}")
    if problems:
        return _result(
            "FAIL", "; ".join(problems), invocation.command("init")
        ), settings
    return _result("ok", "relay setup is complete"), settings


def on_path(
    command: list[str], which: Callable[[str], str | None] | None = None
) -> bool:
    """True when the agent command's program is installed on PATH."""
    if not command:
        return False
    return (which or shutil.which)(command[0]) is not None


def login_checkable(adapter: adapters.Adapter, command: list[str]) -> bool:
    """True when the adapter has a login check that applies to this command;
    a command that wraps the agent in another program cannot be checked."""
    return (
        bool(command)
        and adapter.login_argv is not None
        and Path(command[0]).name == adapter.binary
    )


def login_state(
    root: Path, adapter: adapters.Adapter, runner: Runner
) -> tuple[bool | None, str]:
    """Run the adapter's login check: (True, "") logged in, (False, "") not,
    or (None, why) when the check itself could not run or timed out."""
    try:
        result = runner(
            list(adapter.login_argv or ()),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None, "timed out after 20 seconds"
    except (FileNotFoundError, OSError) as error:
        return None, str(error)
    return result.returncode == 0, ""


def _programs(settings: config.Config | None) -> tuple[list[Check], list[str]]:
    if settings is None:
        return [], []
    checks: list[Check] = []
    programs: list[str] = []
    for _, (command, backup_for) in _agents_in_use(settings).items():
        if not command:
            checks.append(
                _result(
                    "FAIL",
                    "configured agent command is empty",
                    invocation.command("init"),
                )
            )
            continue
        program = command[0]
        if program in programs:
            continue
        programs.append(program)
        if not on_path(command):
            checks.append(
                _result(
                    "FAIL",
                    f"{program} (backup for {backup_for}) is not on PATH"
                    if backup_for is not None
                    else f"{program} is not on PATH",
                    f"install {program} and make sure it is on PATH",
                )
            )
        else:
            checks.append(_result("ok", f"{program} is on PATH"))
    return checks, programs


def _logins(root: Path, settings: config.Config, runner: Runner) -> list[Check]:
    checks: list[Check] = []
    seen: set[str] = set()
    logged_in: set[str] = set()
    for agent, (command, backup_for) in _agents_in_use(settings).items():
        adapter = config.adapter_for(settings, agent)
        if not login_checkable(adapter, command):
            continue
        if adapter.binary in seen:
            if adapter.binary in logged_in:
                failover.clear_auth_limit(root, agent)
            continue
        program = adapter.binary
        seen.add(program)
        fix = adapter.login_fix
        logged_in_now, problem = login_state(root, adapter, runner)
        if logged_in_now is None:
            checks.append(
                _result("warn", f"could not check {program} login: {problem}", fix)
            )
            continue
        if logged_in_now:
            checks.append(_result("ok", f"{program} is logged in"))
            logged_in.add(program)
            failover.clear_auth_limit(root, agent)
        else:
            message = (
                f"{program} (backup for {backup_for}) is not logged in"
                if backup_for is not None
                else f"{program} is not logged in"
            )
            checks.append(_result("FAIL", message, fix))
    return checks


def _role_checks(root: Path, settings: config.Config) -> list[Check]:
    checks: list[Check] = []
    roles = settings.roles
    agents = _agents_in_use(settings)

    if settings.pipeline is None and roles.implementer == roles.reviewer:
        checks.append(
            _result(
                "warn",
                f"{roles.implementer} is both the implementer and the reviewer, "
                "so the review is not independent",
            )
        )

    for agent, (command, _) in agents.items():
        adapter = config.adapter_for(settings, agent)
        refusal = _bypass_result(agent, adapter.name, command)
        if refusal is not None:
            checks.append(refusal)
        if adapter.name == "generic" and agent == "antigravity":
            checks.append(
                _result(
                    "warn",
                    "antigravity is a generic agent and needs agy 1.4.12 or later "
                    "(github.com/google-antigravity/antigravity-cli#548). The relay "
                    "gives it a per-run allow list; a refused tool call is reported "
                    "but agy does not name the command, and the relay does not "
                    "manage its login -- see README, 'Using Antigravity today'",
                )
            )
        elif adapter.name == "generic" and agent == "grok":
            checks.append(
                _result(
                    "warn",
                    "grok is a generic agent, and it has no non-interactive "
                    "login-status check or specific denial detail -- see README, "
                    "'Using Grok today'. The relay does not manage its "
                    "permissions, login or denials",
                )
            )
        elif adapter.name == "generic":
            checks.append(
                _result(
                    "warn",
                    f"{agent} is a generic agent: the relay does not manage its "
                    "permissions, login or denials",
                )
            )

    if settings.release_role != "human":
        agent = settings.release_role
        command = settings.agents.get(agent) or []
        if agent == "codex" and any("workspace-write" in arg for arg in command):
            checks.append(
                _result(
                    "warn",
                    "release tasks by codex: its workspace-write sandbox blocks network access and tag writes; release tasks will likely fail",
                )
            )
        else:
            checks.append(
                _result(
                    "warn",
                    f"release tasks by {agent}: can't verify that it can push and tag; the first release task will show",
                )
            )

    if roles == config.Roles():
        return checks

    prompt_dir = prompts.prompts_dir(root)
    for name in ("implement.md", "review.md"):
        template = prompt_dir / name
        if not template.exists():
            continue
        text = template.read_text(encoding="utf-8")
        if "{implementer}" not in text or "{reviewer}" not in text:
            checks.append(
                _result(
                    "FAIL",
                    f"prompt template .whyline/relay/prompts/{name} does not use "
                    "{implementer} and {reviewer}, so an agent would be told the "
                    "wrong names",
                    f"{invocation.command('init')} --overwrite",
                )
            )

    for agent, (_, backup_for) in agents.items():
        adapter = config.adapter_for(settings, agent)
        if adapter.name == "generic":
            continue
        role = (
            f"{backup_for} backup"
            if backup_for is not None
            else ("implementer" if agent == roles.implementer else "reviewer")
        )
        checks.append(_result("ok", adapters.describe(adapter, role, agent)))

    return checks


def _role_agents(settings: config.Config) -> list[str]:
    """Agents that fill a role, once each, in role order. Not the backup chain."""
    agents: list[str] = []
    for agent in roles.current_roles(settings).values():
        if agent not in agents:
            agents.append(agent)
    return agents


def _task_repo_reachability(
    root: Path, tasks: list[plan.Task], settings: config.Config | None
) -> list[Check]:
    """Whether each role agent can be given an unchecked task's repository.

    The failure text and the path rule both come from ``loop.task_repo``.
    A repository that resolves to this checkout needs no directory flag:
    the run adds none, because the agent is already there. Any other
    repository is reported once; the line names the path, not the task.
    """
    checks: list[Check] = []
    if settings is None:
        loaded: config.Config | None = None
        agents: list[str] = []
    else:
        loaded = settings
        agents = _role_agents(loaded)
    seen: set[Path] = set()
    root = root.resolve()
    for task in tasks:
        if task.checked or not task.repo:
            continue
        try:
            repo = loop.task_repo(root, task)
        except loop.Paused as paused:
            checks.append(_result("FAIL", paused.reason))
            continue
        if repo == root or repo in seen:
            continue
        seen.add(repo)
        if loaded is None:
            continue
        shown = str(repo)
        for agent in agents:
            flag = config.adapter_for(loaded, agent).dir_flag
            if flag:
                checks.append(
                    _result("ok", f"{agent} gets {' '.join(flag)} {shown}")
                )
            else:
                checks.append(
                    _result(
                        "warn",
                        f"{agent} has no way to be given {shown}; its sandbox "
                        "may refuse writes there",
                        f"set dir_flag in [agents.{agent}] or allow that path in its command",
                    )
                )
    return checks


def _plan_checks(
    plan_path: Path,
    pipeline: "pipeline_module.Pipeline | None" = None,
    *,
    root: Path,
    settings: config.Config | None,
) -> list[Check]:
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
        if task.task_id == planner.PLAN_TASK_ID:
            checks.append(
                _result(
                    "warn",
                    f"task {task.task_id!r} uses the reserved id the planner uses for its "
                    "own handoffs; a real plan.md task with this id can be silently "
                    "confused with a plan-drafting session",
                )
            )
        if task.profile is not None:
            if task.profile == "release":
                pass
            elif pipeline is None:
                checks.append(
                    _result(
                        "warn",
                        f"task {task.task_id!r} names relay-profile {task.profile!r}, "
                        "but no [pipeline] is configured; it will be ignored",
                    )
                )
            elif (
                task.profile not in pipeline.profiles
                # `verify` is a marker. With no profile of that name, _profile_for
                # runs the default profile, so startup must let that task through.
                and loop._profile_for(task, pipeline) not in pipeline.profiles
            ):
                checks.append(
                    _result(
                        "FAIL",
                        f"task {task.task_id!r} names relay-profile {task.profile!r}, "
                        "which is not in [pipeline.profiles]",
                    )
                )
    checks.extend(_task_repo_reachability(root, tasks, settings))
    return checks


def _bypass_result(agent: str, adapter_name: str, command: list[str]) -> Check | None:
    found = bypass.find(adapter_name, command)
    if not found:
        return None
    return _result(
        "FAIL",
        f"refusing to run {agent}: its command contains a "
        f"permission-bypass flag ({', '.join(found)}). The relay never "
        "runs an agent that way",
        "remove it from .whyline/relay/config.toml",
    )


def run(
    root: Path,
    plan_path: Path | None = None,
    *,
    allow_dirty: bool = False,
    runner: Runner = subprocess.run,
    scope: str = "full",
) -> list[Check]:
    """Run preflight checks, returning results in display order.

    ``full`` is every check, including whether each role agent can be given
    an unchecked task's ``relay-repo``. ``repository`` is the git repo,
    whyline, relay config, permission-bypass, clean-tree, and live-relay
    checks.
    """
    if scope not in ("full", "repository"):
        raise ValueError(f"unknown preflight scope: {scope!r}")
    if scope == "repository" and allow_dirty:
        raise ValueError("allow_dirty is not valid for preflight scope 'repository'")
    root = root.resolve()
    results: list[Check] = []

    def attempt(name: str, check: Callable[[], Any]) -> Any:
        try:
            return check()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            results.append(_crashed(name, error))
            return None

    def add(name: str, check: Callable[[], list[Check]]) -> None:
        results.extend(attempt(name, check) or [])

    add(
        "git check",
        lambda: [
            _result("ok", "directory is inside a git repository")
            if _inside_git(root)
            else _result(
                "FAIL",
                "directory is not inside a git repository",
                "run it inside a git repository",
            )
        ],
    )
    add("whyline check", lambda: [_whyline(root, runner)])
    settings: config.Config | None = None
    setup_result = attempt("relay setup check", lambda: _relay_setup(root))
    if setup_result is not None:
        setup, settings = setup_result
        results.append(setup)
    if scope == "full":
        add("program checks", lambda: _programs(settings)[0])
        if settings is not None:
            loaded = settings
            add("login checks", lambda: _logins(root, loaded, runner))
            add("role checks", lambda: _role_checks(root, loaded))
            add("pipeline stage checks", lambda: _stage_checks(root, loaded))

        def plan_checks() -> list[Check]:
            selected_plan = plan_path
            if selected_plan is None:
                selected_plan = root / (
                    settings.plan if settings is not None else config.DEFAULTS["plan"]
                )
            elif not selected_plan.is_absolute():
                selected_plan = root / selected_plan
            return _plan_checks(
                selected_plan,
                settings.pipeline if settings is not None else None,
                root=root,
                settings=settings,
            )

        add("plan checks", plan_checks)
    elif settings is not None:
        loaded = settings

        def bypass_checks() -> list[Check]:
            found: list[Check] = []
            for agent, (command, _) in _agents_in_use(loaded).items():
                adapter = config.adapter_for(loaded, agent)
                refusal = _bypass_result(agent, adapter.name, command)
                if refusal is not None:
                    found.append(refusal)
            return found

        add("permission-bypass checks", bypass_checks)

    add("working tree check", lambda: _tree_check(root, scope, allow_dirty))

    def disk_check() -> list[Check]:
        low_disk = agents.low_disk_message(shutil.disk_usage(root).free)
        return [] if low_disk is None else [_result("FAIL", low_disk)]

    add("disk space check", disk_check)

    def live_check() -> list[Check]:
        active = running.live(root)
        return [
            _result(
                "FAIL",
                f"another relay is running here (pid {active.pid})",
                f"wait for it, or run: {invocation.command('stop')}",
            )
            if active is not None
            else _result("ok", "no other relay is running here")
        ]

    add("running relay check", live_check)
    return results


def _crashed(name: str, error: Exception) -> Check:
    lines = str(error).splitlines()
    first = lines[0][:200] if lines else ""
    return _result(
        "FAIL",
        f"{name} could not run: {type(error).__name__}: {first}",
        f"run {invocation.command('doctor')} again; if it repeats, report it",
    )


def _stage_checks(root: Path, settings: config.Config) -> list[Check]:
    found: list[Check] = []
    if settings.pipeline is None:
        return found
    for stage in settings.pipeline.stages.values():
        try:
            prompts.load(root, stage.prompt)
        except prompts.PromptError as error:
            found.append(
                _result(
                    "FAIL",
                    str(error),
                    f"add .whyline/relay/prompts/{stage.prompt}.md, or name a "
                    "built-in prompt (implement, review) instead",
                )
            )
    return found


def _tree_check(root: Path, scope: str, allow_dirty: bool) -> list[Check]:
    if scope != "repository" and allow_dirty:
        return [_result("ok", "working-tree check skipped (--allow-dirty)")]
    try:
        paths = gitcheck.blocking_dirty_paths(root)
    except gitcheck.GitError as error:
        return [_result("FAIL", f"could not check working tree: {error}")]
    if scope == "repository":
        message = "working tree has uncommitted changes: " + ", ".join(paths)
        hint = "commit or stash your changes"
    else:
        message = "working tree has uncommitted changes"
        hint = "commit or stash your changes, or pass --allow-dirty"
    if not paths:
        return [_result("ok", "working tree is clean")]
    return [_result("FAIL", message, hint)]


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
