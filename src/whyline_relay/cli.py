"""Command line entry point."""

from __future__ import annotations

import argparse
import shlex
import signal
import sys
from dataclasses import replace
from pathlib import Path

from whyline_relay import agents, config, gitcheck, init, loop, notify, plan, prompts, state, whylinecmd

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PAUSED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whyline-relay")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Run the plan from its first unchecked task")
    start.add_argument("--repo", default=".", help="Repository root (default: cwd)")
    start.add_argument("--plan", default=None, help="Plan file (default: from config)")
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--only", default=None, metavar="TASK_ID")
    start.add_argument("--branch", default=None)
    start.add_argument("--allow-main", action="store_true")
    start.add_argument("--allow-dirty", action="store_true")
    start.add_argument("--max-rounds", type=int, default=None)
    start.add_argument("--timeout", type=int, default=None, metavar="MIN")

    resume = subparsers.add_parser("resume", help="Continue after a pause")
    resume.add_argument("--repo", default=".")
    resume.add_argument("--allow-dirty", action="store_true")

    status = subparsers.add_parser("status", help="Where the relay is")
    status.add_argument("--repo", default=".")

    stop = subparsers.add_parser("stop", help="Stop after the current agent finishes")
    stop.add_argument("--repo", default=".")

    init_parser = subparsers.add_parser("init", help="Write permissions and templates")
    init_parser.add_argument("--repo", default=".")
    init_parser.add_argument("--yes", action="store_true")
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
    saved = state.load(root)
    if saved is None:
        print("Nothing to resume.", file=sys.stderr)
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
    except (gitcheck.GitError, whylinecmd.WhylineUnavailable, plan.PlanError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_ERROR
    print("Plan complete.")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    saved = state.load(root)
    if saved is None:
        print("No relay run in progress.")
        return EXIT_OK
    print(f"Task      {saved.task_id}")
    print(f"Branch    {saved.branch}")
    print(f"Plan      {saved.plan}")
    print(f"Paused    {saved.paused_reason}")
    if saved.log_path:
        print(f"Log       {saved.log_path}")
    print("Resume with: whyline-relay resume")
    return EXIT_OK


def cmd_stop(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    target = loop.stop_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    print("STOP written. The current agent finishes; nothing new starts.")
    return EXIT_OK


def cmd_init(args: argparse.Namespace) -> int:
    return init.run(Path(args.repo).resolve(), assume_yes=args.yes)


def _report_pause(paused: loop.Paused) -> int:
    print(f"\nPaused: {paused.reason}", file=sys.stderr)
    if paused.log_path is not None:
        print(f"Log: {paused.log_path}", file=sys.stderr)
    print("Resume with: whyline-relay resume", file=sys.stderr)
    notify.send("whyline-relay paused", paused.reason)
    return EXIT_PAUSED


def cmd_start(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
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
        )
        argv = agents.build_argv(settings.agents["codex"], rendered)
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
    except (gitcheck.GitError, whylinecmd.WhylineUnavailable, plan.PlanError) as error:
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _install_sigint_handler()
    commands = {
        "start": cmd_start,
        "resume": cmd_resume,
        "status": cmd_status,
        "stop": cmd_stop,
        "init": cmd_init,
    }
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        print("\nInterrupted. Resume with: whyline-relay resume", file=sys.stderr)
        return EXIT_PAUSED


def entry() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(entry())
