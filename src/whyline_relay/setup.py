"""The `whyline-relay setup` wizard: plan source, roles, then a doctor gate."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

from whyline_relay import agents, brainstorm, chat, config, gitcheck, plan, planner, preflight

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


def role_paths(root: Path) -> list[Path]:
    relay = config.relay_dir(root)
    return [relay / "config.toml", relay / "prompts" / "test.md"]


def _has_pipeline(text: str) -> bool:
    try:
        return "pipeline" in tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False


def _set_keys(text: str, table: str, values: dict[str, str]) -> str:
    """Sets `key = "value"` lines inside [table], keeping every other line
    (comments, other keys, alignment) as it was. Missing keys are added at
    the end of the table."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == f"[{table}]"), None)
    if start is None:
        return text.rstrip("\n") + f"\n\n[{table}]\n" + "".join(
            f'{key} = "{value}"\n' for key, value in values.items()
        )
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("["):
        end += 1
    remaining = dict(values)
    for index in range(start + 1, end):
        for key in list(remaining):
            match = re.match(rf"^(\s*{key}\s*=\s*).*$", lines[index])
            if match:
                lines[index] = f'{match.group(1)}"{remaining.pop(key)}"'
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines[insert_at:insert_at] = [f'{key} = "{value}"' for key, value in remaining.items()]
    return "\n".join(lines) + "\n"


def _set_backup(text: str, chain: list[str]) -> str:
    """Replaces the [backup] table (or removes it when `chain` is empty)."""
    lines = text.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == "[backup]":
            index += 1
            while index < len(lines) and not lines[index].lstrip().startswith("["):
                index += 1
            continue
        kept.append(lines[index])
        index += 1
    content = "\n".join(kept).rstrip("\n") + "\n"
    if chain:
        chain_toml = ", ".join(f'"{name}"' for name in chain)
        content += f"\n[backup]\nchain = [{chain_toml}]\n"
    return content


def write_roles(
    root: Path,
    implementer: str,
    tester: str,
    reviewer: str,
    backup=(),
    *,
    commit: bool = True,
) -> list[Path]:
    """Writes the role assignments without asking anything. A config with its
    own [pipeline] keeps everything except the three role keys and the
    backup chain; anything else gets the default pipeline template. Commits
    only these files."""
    config_file, test_prompt = role_paths(root)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    existing = config_file.read_text(encoding="utf-8") if config_file.exists() else ""
    if _has_pipeline(existing):
        content = _set_keys(existing, "roles", {
            "implementer": implementer, "tester": tester, "reviewer": reviewer,
        })
    else:
        content = PIPELINE_CONFIG_TEMPLATE.format(
            implementer=implementer, tester=tester, reviewer=reviewer
        )
    content = _set_backup(content, [name for name in backup if name])
    config_file.write_text(content, encoding="utf-8")
    test_prompt.parent.mkdir(parents=True, exist_ok=True)
    test_prompt.write_text(TEST_PROMPT_TEMPLATE, encoding="utf-8")
    if commit:
        gitcheck.commit_paths(
            root, [config_file, test_prompt],
            "setup: assign implementer/tester/reviewer roles",
        )
    return [config_file, test_prompt]


def _run_brainstorm_plan_source(
    root: Path,
    settings: "config.Config",
    *,
    input_fn=None,
    print_fn=None,
    run_fn=None,
    runner=None,
    confirm=None,
) -> bool:
    """The "brainstorm" choice: runs the existing brainstorm engine
    unchanged, then turns its final synthesis into a real plan.md via the
    shared review gate (RCP1)."""
    input_fn = input_fn if input_fn is not None else input
    print_fn = print_fn if print_fn is not None else print
    # The review gate shares the wizard's input stream unless the caller
    # passes its own confirm. Otherwise an answer sequence that approves
    # the draft never reaches review_gate.
    confirm = confirm if confirm is not None else input_fn

    setup_answers = brainstorm.ask_brainstorm_setup(
        root, settings, input_fn=input_fn, print_fn=print_fn
    )
    if setup_answers is None:
        return (root / settings.plan).exists()

    topic = setup_answers["topic"]
    models = setup_answers["models"]
    passes = setup_answers["passes"]
    final_agent = setup_answers["final_agent"]
    timeout_seconds = setup_answers.get("timeout_seconds")
    kwargs = {"run_fn": run_fn} if run_fn is not None else {}
    if runner is not None:
        kwargs["runner"] = runner
    if timeout_seconds is not None:
        kwargs["timeout_seconds"] = timeout_seconds

    actual_agents = brainstorm.run_pass_zero(
        root, models, topic, settings=settings, print_fn=print_fn, **kwargs
    )
    if not actual_agents:
        print_fn(
            "No selected agent succeeded; stopping brainstorm without synthesis. "
            "Check agent availability, authentication, or quotas and try again."
        )
        return (root / settings.plan).exists()

    brainstorm.merge_pass_zero(root, models, topic, actual_agents=actual_agents)
    for pass_number in range(1, passes + 1):
        actual_agents = brainstorm.run_review_pass(
            root, models, topic, pass_number, settings=settings,
            print_fn=print_fn, actual_agents=actual_agents, **kwargs,
        )
    try:
        record = brainstorm.run_final_synthesis(
            root, final_agent, models, topic, settings=settings,
            print_fn=print_fn, actual_agents=actual_agents, **kwargs,
        )
    except brainstorm.NothingToSynthesize as error:
        print_fn(str(error))
        return (root / settings.plan).exists()
    except (agents.AgentMissing, agents.AgentTimeout, chat.AgentUnavailable) as error:
        print_fn(f"Could not generate a synthesis: {error}")
        return (root / settings.plan).exists()

    if not record or not record.get("ok"):
        return (root / settings.plan).exists()

    synthesizer = record.get("agent", final_agent)
    try:
        draft = brainstorm.generate_plan_from_synthesis(
            root, settings, synthesizer, models, topic, **kwargs
        )
    except brainstorm.NothingToSynthesize as error:
        print_fn(str(error))
        return (root / settings.plan).exists()
    except (agents.AgentMissing, agents.AgentTimeout, chat.AgentUnavailable) as error:
        print_fn(f"Could not generate a plan from the synthesis: {error}")
        return (root / settings.plan).exists()
    except plan.PlanError as error:
        print_fn(
            f"The drafted plan still did not parse after retrying: {error}. "
            f"Left at {brainstorm.plan_draft_path(root)} for you to fix by hand."
        )
        return (root / settings.plan).exists()

    def revise(feedback: str) -> None:
        brainstorm.generate_plan_from_synthesis(
            root, settings, synthesizer, models, topic, feedback=feedback, **kwargs
        )

    result = planner.review_gate(
        root, settings, draft, topic,
        drafted_by=f"brainstorm ({synthesizer})",
        revise_fn=revise,
        confirm=confirm,
    )
    print_fn(result)
    return (root / settings.plan).exists()


def choose_plan_source(
    root: Path,
    settings: "config.Config",
    *,
    input_fn=None,
    print_fn=None,
    planner_start=None,
    run_fn=None,
    runner=None,
    confirm=None,
) -> bool:
    """Ask whether to use an existing plan, draft one, or brainstorm one.
    Returns True once a plan file exists and setup should continue; False
    otherwise."""
    input_fn = input_fn if input_fn is not None else input
    print_fn = print_fn if print_fn is not None else print
    planner_start = planner_start if planner_start is not None else planner.start

    plan_path = root / settings.plan
    default_choice = "existing" if plan_path.exists() else "draft"
    choice = (
        input_fn(
            f"Use the existing {settings.plan}, draft a new one, or "
            f"brainstorm one? [{default_choice}]: "
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

    if choice == "brainstorm":
        return _run_brainstorm_plan_source(
            root, settings, input_fn=input_fn, print_fn=print_fn,
            run_fn=run_fn, runner=runner, confirm=confirm,
        )

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
    config_path, test_prompt_path = write_roles(
        root, implementer, tester, reviewer, backup_chain, commit=False
    )
    print_fn(f"Wrote {config_path.relative_to(root)}.")
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

    if gitcheck.commit_paths(
        root, role_paths(root), "setup: assign implementer/tester/reviewer roles"
    ):
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
