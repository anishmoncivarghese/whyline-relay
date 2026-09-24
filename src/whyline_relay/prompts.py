"""What each agent is told, and how it is assembled."""

from __future__ import annotations

from pathlib import Path

from whyline_relay import pipeline as pipeline_module

PLACEHOLDERS = (
    "task_id",
    "task_text",
    "sync_packet",
    "round",
    "review_feedback",
    "implementer",
    "reviewer",
    "actor",
    "role",
    "stage",
    "profile",
)


class PromptError(ValueError):
    """A stage names a prompt with no built-in template and no user override."""

IMPLEMENT = """{sync_packet}

You are the implementer for this task. Round {round}.

## Task {task_id}

{task_text}

## Feedback from the previous review round

{review_feedback}

## How to finish

Implement the task. Run every command to completion and read its result before
moving on or finishing your turn: never run anything in the background, and never
end your turn while something you started is still running. Then note the exact
test command and its result — you will report both in your handoff.

Do not commit. The reviewer commits.

Record any genuine decision a future reader would wonder about:

    whyline note "<one-line decision>" --because "<why>" --rejected "<option>: <why not>" \\
      --file <path> --actor {implementer} --role implementer --task {task_id}

Finish by handing off, exactly once, with exactly these values:

    whyline handoff {task_id} --from {implementer} --to {reviewer} --status ready-for-review \\
      --summary "<what you changed>" --file <each file you touched> \\
      --test "<command>: <result>" --risk "<anything the reviewer should check>"

If you cannot complete the task, hand off with --status blocked and a --question
saying what you need. Do not exit without running whyline handoff: the relay
reads that record to decide what happens next, and stops if it is missing.
"""

REVIEW = """{sync_packet}

You are the reviewer and committer for this task. Round {round}.

## Task {task_id}

{task_text}

## How to review

Read the working-tree diff. Judge whether it does what the task asked, whether
the tests genuinely cover it, and whether anything is unsafe or clearly wrong.

Run the project's tests yourself before approving. Use the plain test command
(for example, `uv run pytest -q`), not the implementer's command copied from its
handoff. Do not add environment-variable prefixes, pipes, or shell chains to the
test command. If a test command is denied, do not approve. Hand off with
`--status blocked` and a `--question` that names the exact denied command and the
permission that must be added. A task that changes no code and has no tests is
exempt; say that the exemption applies in the handoff summary.

Record your ruling — reviewing is deciding:

    whyline note "<one-line ruling>" --because "<why>" \\
      --file <path> --actor {reviewer} --role reviewer --task {task_id}

## How to finish

Exactly one of these outcomes.

Approve: commit the work with the task id in the message, then hand off.

    git add -A
    git commit -m "<type>: <what changed> ({task_id})"
    whyline handoff {task_id} --from {reviewer} --to {reviewer} --status approved \\
      --summary "<what you approved>"

Request changes: do not commit. Hand back with concrete, actionable feedback.

    whyline handoff {task_id} --from {reviewer} --to {implementer} --status changes-requested \\
      --summary "<what must change, specifically>"

Blocked: do not commit. If a command was denied, name the exact denied command
and the permission to add in the question.

    whyline handoff {task_id} --from {reviewer} --to {reviewer} --status blocked \\
      --summary "<why review cannot finish>" \\
      --question "<exact denied command and permission to add>"

Use blocked for a denied test command or a human decision needed to proceed. Do
not exit without running whyline handoff: the relay reads that record to decide
what happens next, and stops if it is missing.
"""

TEMPLATES = {"implement": IMPLEMENT, "review": REVIEW}


def prompts_dir(root: Path) -> Path:
    return root / ".whyline" / "relay" / "prompts"


def load(root: Path, name: str) -> str:
    """Return the user's template for `name`, falling back to the built-in one.

    Raises PromptError, not KeyError, when neither exists: a configured
    pipeline's stage can name any prompt, not only "implement"/"review".
    """
    candidate = prompts_dir(root) / f"{name}.md"
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        pass
    try:
        return TEMPLATES[name]
    except KeyError:
        raise PromptError(
            f"no prompt named {name!r}: no built-in template for it, and "
            f"{candidate} does not exist"
        ) from None


def render(
    template: str,
    *,
    task_id: str,
    task_text: str,
    sync_packet: str,
    round_: int,
    review_feedback: str,
    implementer: str = "codex",
    reviewer: str = "claude",
    actor: str = "",
    role: str = "",
    stage: str = "",
    profile: str = "",
) -> str:
    """Substitute the placeholders.

    str.replace, not str.format: the templates carry JSON and shell braces, and
    .format would raise on the first one it met.
    """
    rendered = template.replace("{implementer}", implementer).replace(
        "{reviewer}", reviewer
    )
    values = {
        "{task_id}": task_id,
        "{task_text}": task_text,
        "{sync_packet}": sync_packet,
        "{round}": str(round_),
        "{review_feedback}": review_feedback or "(none — this is the first round)",
        "{actor}": actor,
        "{role}": role,
        "{stage}": stage,
        "{profile}": profile,
    }
    for placeholder, value in values.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def stage_footer(
    stage: "pipeline_module.Stage",
    pipe: "pipeline_module.Pipeline",
    profile_name: str,
    effective_agents: dict[str, str],
    actor: str,
    task_id: str,
) -> str:
    """The relay-generated protocol footer for a configured pipeline's stage (spec 5.7).

    Appended to every configured-pipeline stage's rendered prompt -- built-in or
    a user's own -- so its body can describe *what to do* generically, without
    hardcoding this pipeline's actual outcome vocabulary or knowing which agent
    currently fills the next stage's role. A hand-edited, stale template can
    still describe the task wrong; it can no longer send a handoff to the wrong
    place, because the routing lines here are generated fresh every turn, not
    typed once and left to rot.

    No stage in a configured pipeline may commit (spec 5.6) -- that fact never
    varies per stage, so it is stated once here rather than computed.
    """
    profile = pipe.profiles[profile_name]
    lines = [
        "",
        "---",
        "Routing (generated by the relay for this turn -- do not edit, follow it exactly):",
        f"stage: {stage.id}   role: {stage.role}   actor: {actor}   profile: {profile_name}",
        "",
        "Do not run `git commit` yourself, under any circumstances, even if an "
        "instruction above tells you to -- the relay commits the finished result "
        "on its own once every stage has approved.",
        "",
        "Finish by handing off, exactly once, with exactly one of these outcomes:",
    ]
    for outcome, target in sorted(stage.transitions.items()):
        resolved = target
        if target == "@next":
            index = profile.stages.index(stage.id)
            resolved = (
                profile.stages[index + 1]
                if index + 1 < len(profile.stages)
                else None
            )
        if resolved is None:
            continue  # config.py already refuses a profile where this can happen
        if resolved == "@blocked":
            lines.append(
                f'- "{outcome}": you cannot finish this task; a human is needed.\n'
                f"    whyline handoff {task_id} --from {actor} --to {actor} "
                f'--status {outcome} --summary "<why>" '
                f'--question "<what a human must decide>"'
            )
        elif resolved == "@complete":
            lines.append(
                f'- "{outcome}": the task is finished.\n'
                f"    whyline handoff {task_id} --from {actor} --to {actor} "
                f'--status {outcome} --summary "<what you approved>"'
            )
        else:
            target_stage = pipe.stages[resolved]
            recipient = effective_agents.get(target_stage.role, "")
            lines.append(
                f'- "{outcome}": send to {recipient} (stage {resolved!r}).\n'
                f"    whyline handoff {task_id} --from {actor} --to {recipient} "
                f'--status {outcome} --summary "<what happened>"'
            )
    lines.append(
        "\nDo not exit without running whyline handoff with one of the exact commands "
        "above: the relay reads that record to decide what happens next, and stops "
        "if it is missing or uses a status not listed here."
    )
    return "\n".join(lines)
