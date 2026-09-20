"""What each agent is told, and how it is assembled."""

from __future__ import annotations

from pathlib import Path

PLACEHOLDERS = ("task_id", "task_text", "sync_packet", "round", "review_feedback")

IMPLEMENT = """{sync_packet}

You are the implementer for this task. Round {round}.

## Task {task_id}

{task_text}

## Feedback from the previous review round

{review_feedback}

## How to finish

Implement the task. Run the project's tests and note the exact command and its
result — you will report both in your handoff.

Do not commit. The reviewer commits.

Record any genuine decision a future reader would wonder about:

    whyline note "<one-line decision>" --because "<why>" --rejected "<option>: <why not>" \\
      --file <path> --actor codex --role implementer --task {task_id}

Finish by handing off, exactly once, with exactly these values:

    whyline handoff {task_id} --from codex --to claude --status ready-for-review \\
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

Record your ruling — reviewing is deciding:

    whyline note "<one-line ruling>" --because "<why>" \\
      --file <path> --actor claude --role reviewer --task {task_id}

## How to finish

Exactly one of these two outcomes.

Approve: commit the work with the task id in the message, then hand off.

    git add -A
    git commit -m "<type>: <what changed> ({task_id})"
    whyline handoff {task_id} --from claude --to claude --status approved \\
      --summary "<what you approved>"

Request changes: do not commit. Hand back with concrete, actionable feedback.

    whyline handoff {task_id} --from claude --to codex --status changes-requested \\
      --summary "<what must change, specifically>"

If the task is blocked on a human decision, hand off with --status blocked and a
--question. Do not exit without running whyline handoff: the relay reads that
record to decide what happens next, and stops if it is missing.
"""

TEMPLATES = {"implement": IMPLEMENT, "review": REVIEW}


def prompts_dir(root: Path) -> Path:
    return root / ".whyline" / "relay" / "prompts"


def load(root: Path, name: str) -> str:
    """Return the user's template for `name`, falling back to the built-in one."""
    candidate = prompts_dir(root) / f"{name}.md"
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return TEMPLATES[name]


def render(
    template: str,
    *,
    task_id: str,
    task_text: str,
    sync_packet: str,
    round_: int,
    review_feedback: str,
) -> str:
    """Substitute the five placeholders.

    str.replace, not str.format: the templates carry JSON and shell braces, and
    .format would raise on the first one it met.
    """
    values = {
        "{task_id}": task_id,
        "{task_text}": task_text,
        "{sync_packet}": sync_packet,
        "{round}": str(round_),
        "{review_feedback}": review_feedback or "(none — this is the first round)",
    }
    rendered = template
    for placeholder, value in values.items():
        rendered = rendered.replace(placeholder, value)
    return rendered
