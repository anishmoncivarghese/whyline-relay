"""Command line entry point."""

from __future__ import annotations

import argparse
import shlex
import signal
import sys
from dataclasses import replace
from datetime import datetime
from importlib import metadata
from pathlib import Path

from whyline_relay import (
    adapters,
    agents,
    config,
    failover,
    gitcheck,
    init,
    invocation,
    loop,
    notify,
    plan,
    planhelp,
    planner,
    preflight,
    prompts,
    remove,
    roles,
    running,
    state,
    whylinecmd,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PAUSED = 2


def _package_version() -> str:
    try:
        return metadata.version("whyline-relay")
    except metadata.PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=invocation.prog())
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_package_version()}",
        help="Show the installed version and exit.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Run the plan from its first unchecked task")
    start.add_argument(
        "--repo", default=".", help="Use this repository root (default: current directory)."
    )
    start.add_argument(
        "--plan", default=None, help="Use this plan file (default: from config)."
    )
    start.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the next task's prompt and command without launching anything.",
    )
    start.add_argument(
        "--only", default=None, metavar="TASK_ID", help="Run only the named task."
    )
    start.add_argument(
        "--branch",
        default=None,
        help="Use this work branch (default: relay/<plan-name>).",
    )
    start.add_argument(
        "--allow-main", action="store_true", help="Allow running on main or master."
    )
    start.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Skip the clean-working-tree check.",
    )
    start.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip the preflight checks.",
    )
    start.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help="Override the review-round limit from config.",
    )
    start.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="MIN",
        help="Override the per-agent timeout from config, in minutes.",
    )

    resume = subparsers.add_parser("resume", help="Continue after a pause")
    resume.add_argument(
        "--repo", default=".", help="Use this repository root (default: current directory)."
    )
    resume.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Skip the clean-working-tree check.",
    )
    resume.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip the preflight checks.",
    )

    doctor = subparsers.add_parser("doctor", help="Check that the relay is ready to run")
    doctor.add_argument(
        "--repo", default=".", help="Use this repository root (default: current directory)."
    )
    doctor.add_argument(
        "--plan", default=None, help="Use this plan file (default: from config)."
    )
    doctor.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Skip the clean-working-tree check.",
    )

    status = subparsers.add_parser("status", help="Where the relay is")
    status.add_argument(
        "--repo", default=".", help="Use this repository root (default: current directory)."
    )

    stop = subparsers.add_parser("stop", help="Stop after the current agent finishes")
    stop.add_argument(
        "--repo", default=".", help="Use this repository root (default: current directory)."
    )

    init_parser = subparsers.add_parser("init", help="Write permissions and templates")
    init_parser.add_argument(
        "--repo", default=".", help="Use this repository root (default: current directory)."
    )
    init_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation question."
    )
    init_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace files that already exist, discarding your edits.",
    )
    init_parser.add_argument(
        "--implementer",
        choices=sorted(adapters.BUILTIN),
        default=None,
        metavar="NAME",
        help="Which built-in agent implements (default: codex)",
    )
    init_parser.add_argument(
        "--reviewer",
        choices=sorted(adapters.BUILTIN),
        default=None,
        metavar="NAME",
        help="Which built-in agent reviews and commits (default: claude)",
    )

    remove_parser = subparsers.add_parser("remove", help="Remove the relay setup")
    remove_parser.add_argument(
        "--repo", default=".", help="Use this repository root (default: current directory)."
    )
    remove_parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation question."
    )
    remove_parser.add_argument(
        "--force", action="store_true", help="Remove even while a run is paused."
    )

    roles_parser = subparsers.add_parser(
        "roles", help="Inspect or reset a backup switch"
    )
    roles_parser.add_argument(
        "--repo",
        default=".",
        help="Use this repository root (default: current directory).",
    )
    roles_sub = roles_parser.add_subparsers(dest="roles_command", required=True)
    roles_status = roles_sub.add_parser(
        "status", help="Show each role's configured and active agent"
    )
    roles_status.add_argument(
        "--repo", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )
    roles_reset = roles_sub.add_parser(
        "reset", help="Clear a sticky backup switch"
    )
    roles_reset.add_argument(
        "role",
        nargs="?",
        default=None,
        help="Reset only this role (default: every role).",
    )
    roles_reset.add_argument(
        "--repo", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )
    roles_set = roles_sub.add_parser(
        "set", help="Permanently point a role at an agent"
    )
    roles_set.add_argument(
        "role",
        help="The role to change (implementer, reviewer, or a configured pipeline role)",
    )
    roles_set.add_argument("--agent", default=None, help="The agent name to use")
    roles_set.add_argument(
        "--model",
        default=None,
        help="Optional model, for a built-in agent name (codex or claude)",
    )
    roles_set.add_argument(
        "--repo", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )

    plan_format = subparsers.add_parser(
        "plan-format", help="Print how to write a plan"
    )
    plan_format.add_argument(
        "--prompt", action="store_true", help="Print only the paste-ready prompt."
    )

    plan_parser = subparsers.add_parser(
        "plan", help="Draft a plan.md from a free-text description"
    )
    plan_parser.add_argument(
        "description", nargs="?", default=None, help="What to build, in plain language."
    )
    plan_parser.add_argument(
        "--repo", default=".", help="Use this repository root (default: current directory)."
    )
    plan_parser.add_argument(
        "--discard",
        action="store_true",
        help="Clear an in-flight plan session without resuming it.",
    )
    return parser


def _task_for(tasks: list[plan.Task], only: str | None) -> plan.Task | None:
    if only is not None:
        return plan.find(tasks, only)
    return plan.next_unchecked(tasks)


def guard(root: Path, args: argparse.Namespace, branch: str) -> str | None:
    """Return a refusal message, or None when it is safe to start.

    `branch` is the branch the agents will commit to, not the one you are on:
    starting from main is normal, because the relay switches to its own branch.
    """
    if branch in ("main", "master") and not getattr(args, "allow_main", False):
        return (
            f"refusing to run on {branch}. Pass --branch NAME to use a different "
            "branch, or --allow-main if you really mean it."
        )
    if gitcheck.is_dirty(root) and not getattr(args, "allow_dirty", False):
        return (
            "refusing to start with uncommitted changes. Commit or stash them, "
            "or pass --allow-dirty."
        )
    return None


def cmd_resume(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    active = running.live(root)
    if active is not None:
        print(
            f"Refusing to resume: another relay is running here (pid {active.pid}). "
            f"Run `{invocation.command('stop')}` first.",
            file=sys.stderr,
        )
        return EXIT_ERROR
    plan_state = state.load_plan(root)
    if plan_state is not None:
        settings = config.load(root)
        try:
            summary = planner.resume(root, settings, plan_state)
        except loop.Paused as paused:
            return _report_pause(paused)
        except (gitcheck.GitError, whylinecmd.WhylineUnavailable) as error:
            print(str(error), file=sys.stderr)
            return EXIT_ERROR
        print(summary)
        return EXIT_OK
    saved = state.load(root)
    if saved is None:
        print("Nothing to resume.", file=sys.stderr)
        return EXIT_ERROR
    if not args.skip_checks:
        checks = _launch_checks(
            root, Path(saved.plan), allow_dirty=args.allow_dirty
        )
        preflight.print_checks(
            checks, stream=sys.stderr, include_ok=False, summary=False
        )
        if preflight.failures(checks):
            return EXIT_ERROR
    loop.stop_path(root).unlink(missing_ok=True)
    settings = config.load(root)
    try:
        # Back onto the branch the run started on: the agents must not commit to
        # whatever happens to be checked out now, which could be main.
        gitcheck.ensure_branch(root, saved.branch)
        loop.run_plan(
            root,
            settings,
            Path(saved.plan),
            branch=saved.branch,
            only=saved.only or None,
            resume=True,
            allow_dirty=args.allow_dirty,
        )
    except loop.Paused as paused:
        return _report_pause(paused)
    except (
        gitcheck.GitError,
        whylinecmd.WhylineUnavailable,
        plan.PlanError,
        running.AlreadyRunning,
    ) as error:
        print(str(error), file=sys.stderr)
        return EXIT_ERROR
    print("Plan complete.")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    active = running.live(root)
    if active is not None:
        try:
            started = datetime.fromisoformat(active.started)
            elapsed = datetime.now(started.tzinfo) - started
            since = started.strftime("%H:%M:%S")
            ago = agents.format_duration(elapsed.total_seconds())
        except ValueError:
            since = active.started
            ago = "unknown"
        if active.role == "reviewer":
            action = "reviewing"
        elif active.role == "implementer":
            action = "implementing"
        else:
            action = "implementing" if active.agent == "codex" else "reviewing"
        print(
            f"Running: {active.agent} {action} {active.task}, round "
            f"{active.round}, since {since} ({ago} ago)"
        )
    saved = state.load(root)
    if saved is None:
        if active is None:
            print("No relay run in progress.")
        return EXIT_OK
    print(f"Task      {saved.task_id}")
    print(f"Branch    {saved.branch}")
    print(f"Plan      {saved.plan}")
    print(f"Paused    {saved.paused_reason}")
    if saved.log_path:
        print(f"Log       {saved.log_path}")
    print(f"Resume with: {invocation.command('resume')}")
    return EXIT_OK


def cmd_stop(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    target = loop.stop_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    print("STOP written. The current agent finishes; nothing new starts.")
    return EXIT_OK


def cmd_init(args: argparse.Namespace) -> int:
    return init.run(
        Path(args.repo).resolve(),
        assume_yes=args.yes,
        overwrite=args.overwrite,
        implementer=args.implementer,
        reviewer=args.reviewer,
    )


def cmd_remove(args: argparse.Namespace) -> int:
    try:
        return remove.run(
            Path(args.repo).resolve(), assume_yes=args.yes, force=args.force
        )
    except gitcheck.GitError as error:
        print(str(error), file=sys.stderr)
        return EXIT_ERROR


def cmd_roles(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    settings = config.load(root)
    if args.roles_command == "status":
        print(roles.status(root, settings))
        return EXIT_OK
    if args.roles_command == "set":
        try:
            print(
                roles.set_role(
                    root, settings, args.role, agent=args.agent, model=args.model
                )
            )
        except roles.RoleSetError as error:
            print(f"Error: {error}", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK
    if args.role is not None and args.role not in roles.current_roles(settings):
        valid = ", ".join(sorted(roles.current_roles(settings)))
        print(
            f"Error: {args.role!r} is not a configured role ({valid})",
            file=sys.stderr,
        )
        return EXIT_ERROR
    print(roles.reset(root, args.role))
    return EXIT_OK


def cmd_plan_format(args: argparse.Namespace) -> int:
    if args.prompt:
        print(planhelp.prompt())
    else:
        print(planhelp.rules())
        print("\nPrompt to give an AI that drafts your plan:")
        print(planhelp.prompt())
    return EXIT_OK


def cmd_plan(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    active = running.live(root)
    if active is not None:
        # Checked before --discard too: discarding a checkpoint out from under
        # a genuinely live turn (a subprocess mid-flight) could race with it --
        # every other command that touches saved state (start, resume, stop)
        # checks this first, before anything else.
        print(
            f"Refusing to touch the plan checkpoint: another relay is running "
            f"here (pid {active.pid}). Run `{invocation.command('stop')}` first.",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if args.discard:
        print(planner.discard(root))
        return EXIT_OK
    if args.description is None:
        print("a description is required (or pass --discard)", file=sys.stderr)
        return EXIT_ERROR
    settings = config.load(root)
    try:
        summary = planner.start(root, settings, args.description)
    except planner.PlanAlreadyInProgress as error:
        print(str(error), file=sys.stderr)
        return EXIT_ERROR
    except loop.Paused as paused:
        return _report_pause(paused)
    except (gitcheck.GitError, whylinecmd.WhylineUnavailable) as error:
        print(str(error), file=sys.stderr)
        return EXIT_ERROR
    print(summary)
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    selected = Path(args.plan) if args.plan is not None else None
    checks = preflight.run(root, selected, allow_dirty=args.allow_dirty)
    preflight.print_checks(checks, stream=sys.stdout, include_ok=True, summary=True)
    return EXIT_ERROR if preflight.failures(checks) else EXIT_OK


def _launch_checks(
    root: Path, plan_path: Path | None, *, allow_dirty: bool
) -> list[preflight.Check]:
    """Indirection keeps command tests from invoking installed login tools."""
    return preflight.run(root, plan_path, allow_dirty=allow_dirty)


def _report_pause(paused: loop.Paused) -> int:
    print(f"\nPaused: {paused.reason}", file=sys.stderr)
    if paused.log_path is not None:
        print(f"Log: {paused.log_path}", file=sys.stderr)
    print(f"Resume with: {invocation.command('resume')}", file=sys.stderr)
    notify.send("whyline-relay paused", paused.reason)
    return EXIT_PAUSED


def cmd_start(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    active = running.live(root)
    if active is not None:
        print(
            f"Refusing to start: another relay is running here (pid {active.pid}). "
            f"Run `{invocation.command('stop')}` first.",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if not args.dry_run and not args.skip_checks:
        selected = Path(args.plan) if args.plan is not None else None
        checks = _launch_checks(root, selected, allow_dirty=args.allow_dirty)
        preflight.print_checks(
            checks, stream=sys.stderr, include_ok=False, summary=False
        )
        if preflight.failures(checks):
            return EXIT_ERROR
    settings = config.load(root)
    if args.max_rounds is not None:
        settings = replace(settings, max_rounds=args.max_rounds)
    if args.timeout is not None:
        settings = replace(settings, timeout_minutes=args.timeout)

    plan_path = root / (args.plan or settings.plan)
    try:
        content = plan_path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"could not read the plan: {error}", file=sys.stderr)
        return EXIT_ERROR
    try:
        tasks = plan.parse(content)
    except plan.PlanError as error:
        print(f"invalid plan: {error}", file=sys.stderr)
        return EXIT_ERROR
    task = _task_for(tasks, args.only)
    if task is None:
        if args.only:
            print(f"no task {args.only!r} in the plan", file=sys.stderr)
            return EXIT_ERROR
        print("Nothing to do: no unchecked tasks in the plan.")
        return EXIT_OK

    if args.dry_run:
        packet = whylinecmd.sync(root, task.task_id)
        rendered = prompts.render(
            prompts.load(root, "implement"),
            task_id=task.task_id,
            task_text=task.text,
            sync_packet=packet,
            round_=1,
            review_feedback="",
            implementer=failover.effective_agent(root, settings, "implementer"),
            reviewer=settings.roles.reviewer,
        )
        argv = agents.build_argv(
            settings.agents[failover.effective_agent(root, settings, "implementer")],
            rendered,
        )
        print(f"Next task: {task.task_id}")
        print(f"Would run: {shlex.join(argv[:-1])} <prompt>")
        print("--- prompt ---")
        print(rendered)
        return EXIT_OK

    branch = args.branch or f"{settings.branch_prefix}{plan_path.stem}"
    refusal = guard(root, args, branch)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return EXIT_ERROR

    try:
        gitcheck.ensure_branch(root, branch)
        outcomes = loop.run_plan(
            root,
            settings,
            plan_path,
            branch=branch,
            only=args.only,
            allow_dirty=args.allow_dirty,
        )
    except loop.Paused as paused:
        return _report_pause(paused)
    except (
        gitcheck.GitError,
        whylinecmd.WhylineUnavailable,
        plan.PlanError,
        running.AlreadyRunning,
    ) as error:
        print(str(error), file=sys.stderr)
        return EXIT_ERROR
    print(f"\nPlan complete: {len(outcomes)} task(s) approved and committed.")
    notify.send("whyline-relay", f"{len(outcomes)} task(s) done")
    return EXIT_OK


def _install_sigint_handler() -> None:
    """Ctrl+C stops the run; the agent's own process group dies with it.

    Agents are started with start_new_session=True, so SIGINT does not reach
    them automatically. loop.run_plan saves state on the way out, which is what
    makes `resume` possible.
    """

    def handler(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handler)


def main(argv: list[str] | None = None, prog: str = "whyline-relay") -> int:
    """Run a relay command and return its exit code.

    This is the supported entry point for embedding the relay. When ``argv`` is
    provided it is parsed directly, without reading ``sys.argv``. This function
    never calls ``sys.exit``; the console wrapper owns process exit behavior.
    """
    with invocation.called_as(prog):
        args = build_parser().parse_args(argv)
        _install_sigint_handler()
        commands = {
            "start": cmd_start,
            "resume": cmd_resume,
            "status": cmd_status,
            "stop": cmd_stop,
            "init": cmd_init,
            "remove": cmd_remove,
            "roles": cmd_roles,
            "doctor": cmd_doctor,
            "plan-format": cmd_plan_format,
            "plan": cmd_plan,
        }
        try:
            return commands[args.command](args)
        except KeyboardInterrupt:
            print(
                f"\nInterrupted. Resume with: {invocation.command('resume')}",
                file=sys.stderr,
            )
            return EXIT_PAUSED


def entry() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(entry())
