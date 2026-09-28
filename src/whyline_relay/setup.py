"""The `whyline-relay setup` wizard: plan source, roles, then a doctor gate."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from whyline_relay import config, gitcheck, planner, preflight

TEST_PROMPT_TEMPLATE = """\x7bsync_packet\x7d

You are the tester for this task. Round \x7bround\x7d.

## Task \x7btask_id\x7d

\x7btask_text\x7d

## How to test

Run the project's own test suite in full, plus anything this task's own
instructions call for. Judge only whether the implementation behaves
correctly -- not whether the diff is well-written; that is the reviewer's
job next.

Record your ruling -- testing is deciding:

    whyline note "<one-line ruling>" --because "<why>" \\
      --file <path> --actor \x7bactor\x7d --role tester --task \x7btask_id\x7d

## How to finish

Exactly one of these outcomes.

Passed: hand off to the reviewer.

    whyline handoff \x7btask_id\x7d --from \x7bactor\x7d --to \x7breviewer\x7d --status passed \\
      --summary "<what you verified>" --test "<command>: <result>"

Failed: hand back to the implementer with concrete, actionable detail.

    whyline handoff \x7btask_id\x7d --from \x7bactor\x7d --to \x7bimplementer\x7d --status failed \\
      --summary "<what failed>" --test "<command>: <result>"

Do not commit either way -- the reviewer commits once this task is fully
approved.
"""

PIPELINE_CONFIG_TEMPLATE = """[roles]
implementer = "\x7bimplementer\x7d"
tester      = "{tester}"
reviewer    = "\x7breviewer\x7d"

[pipeline]
default_profile = "full"

[pipeline.profiles]
full = ["draft", "test", "review"]

[pipeline.stages.draft]
role   = "implementer"
prompt = "implement"
[pipeline.stages.draft.on]
ready = "@next"

[pipeline.stages.test]
role       = "tester"
prompt     = "test"
max_visits = 5
[pipeline.stages.test.on]
passed = "@next"
failed = "draft"

[pipeline.stages.review]
role   = "reviewer"
prompt = "review"
[pipeline.stages.review.on]
approved = "@complete"
rejected = "draft"
"""


def choose_plan_source(
    root: Path,
    settings: "config.Config",
    *,
    input_fn=None,
    print_fn=None,
    planner_start=None,
) -> bool:
    """Ask "use existing or draft a new plan?" and act on it. Returns True
    once a plan file exists and setup should continue; False otherwise."""
    input_fn = input_fn if input_fn is not None else input
    print_fn = print_fn if print_fn is not None else print
    planner_start = planner_start if planner_start is not None else planner.start

    plan_path = root / settings.plan
    default_choice = "existing" if plan_path.exists() else "draft"
    choice = (
        input_fn(
            f"Use the existing {settings.plan}, or draft a new one? "
            f"[{default_choice}]: "
        ).strip().lower()
        or default_choice
    )

    if choice == "draft":
        description = input_fn("What should this plan build? ").strip()
        if not description:
            print_fn("No description given -- nothing drafted.")
            return plan_path.exists()
        try:
            summary = planner_start(root, settings, description)
        except planner.PlanAlreadyInProgress as error:
            print_fn(str(error))
            return plan_path.exists()
        print_fn(summary)
        return plan_path.exists()

    if not plan_path.exists():
        print_fn(
            f"{settings.plan} does not exist -- write one by hand, or run "
            f"`whyline-relay plan \"...\"` to draft one, then run "
            f"`whyline-relay setup` again."
        )
        return False
    return True


def run_role_wizard(root: Path, *, input_fn=None, print_fn=None) -> dict[str, str]:
    """Asks implementer/tester/reviewer/backup, writes config.toml and
    prompts/test.md. Returns the chosen agent names, "backup" as a
    comma-joined string ("" if none was given)."""
    input_fn = input_fn if input_fn is not None else input
    print_fn = print_fn if print_fn is not None else print
    implementer = input_fn("Who implements? [codex]: ").strip() or "codex"
    tester = input_fn("Who tests?      [claude]: ").strip() or "claude"
    reviewer = input_fn("Who reviews?    [claude]: ").strip() or "claude"
    backup_raw = input_fn(
        "Backup chain (comma-separated, blank for none): "
    ).strip()
    backup_chain = [name.strip() for name in backup_raw.split(",") if name.strip()]
    relay = config.relay_dir(root)
    config_path = relay / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = PIPELINE_CONFIG_TEMPLATE.format(
        implementer=implementer, tester=tester, reviewer=reviewer
    )
    if backup_chain:
        chain_toml = ", ".join(f'"{name}"' for name in backup_chain)
        content += f"\n[backup]\nchain = [{chain_toml}]\n"
    config_path.write_text(content, encoding="utf-8")
    print_fn(f"Wrote {config_path.relative_to(root)}.")
    test_prompt_path = relay / "prompts" / "test.md"
    test_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    test_prompt_path.write_text(TEST_PROMPT_TEMPLATE, encoding="utf-8")
    print_fn(f"Wrote {test_prompt_path.relative_to(root)}.")
    return {
        "implementer": implementer,
        "tester": tester,
        "reviewer": reviewer,
        "backup": ", ".join(backup_chain),
    }


def _which(name: str) -> str | None:
    return shutil.which(name)


def _exec(binary: str, argv: list[str]) -> None:
    os.execvp(binary, argv)


def run(
    root: Path,
    *,
    input_fn=None,
    print_fn=None,
    exec_fn=None,
    which=None,
    runner=None,
) -> int:
    """The whole `whyline-relay setup` flow: plan source, roles, an
    auto-committed setup, doctor's gate, then an offer to start."""
    input_fn = input_fn if input_fn is not None else input
    print_fn = print_fn if print_fn is not None else print
    exec_fn = exec_fn if exec_fn is not None else _exec
    which = which if which is not None else _which

    settings = config.load(root)
    if not choose_plan_source(root, settings, input_fn=input_fn, print_fn=print_fn):
        return 1

    run_role_wizard(root, input_fn=input_fn, print_fn=print_fn)

    if gitcheck.commit_all(root, "setup: assign implementer/tester/reviewer roles"):
        print_fn("Committed setup.")

    preflight_kwargs = {} if runner is None else {"runner": runner}
    checks = preflight.run(root, **preflight_kwargs)
    preflight.print_checks(checks, stream=sys.stdout, include_ok=True, summary=True)

    if preflight.failures(checks):
        print_fn("Fix the FAILs above before starting.")
        return 1

    has_warnings = any(check.status == "warn" for check in checks)
    if has_warnings:
        proceed = input_fn("Proceed anyway? [y/N]: ").strip().lower()
        if proceed != "y":
            return 0

    start_choice = input_fn("Ready to start? [Y/n]: ").strip().lower()
    if start_choice in ("", "y", "yes"):
        exec_fn("whyline-relay", ["whyline-relay", "start"])
    return 0
