"""Command line entry point."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from whyline_relay import agents, config, plan, prompts, whylinecmd

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
    return parser


def _task_for(tasks: list[plan.Task], only: str | None) -> plan.Task | None:
    if only is not None:
        return plan.find(tasks, only)
    return plan.next_unchecked(tasks)


def cmd_start(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    settings = config.load(root)
    plan_path = root / (args.plan or settings.plan)
    try:
        content = plan_path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"could not read the plan: {error}", file=sys.stderr)
        return EXIT_ERROR
    tasks = plan.parse(content)
    task = _task_for(tasks, args.only)
    if task is None:
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

    print("Only --dry-run is implemented so far.", file=sys.stderr)
    return EXIT_ERROR


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "start":
        return cmd_start(args)
    return EXIT_ERROR


def entry() -> int:
    return main()


if __name__ == "__main__":
    raise SystemExit(entry())
