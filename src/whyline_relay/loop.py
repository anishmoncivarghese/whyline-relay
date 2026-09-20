"""The state machine. The only module in the relay that decides anything."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from whyline_relay import agents, config, gitcheck, handoff, plan, prompts, routing, whylinecmd


class Paused(RuntimeError):
    """The relay stopped and wants a human. Carries the reason and the log path."""

    def __init__(self, reason: str, log_path: Path | None = None):
        super().__init__(reason)
        self.reason = reason
        self.log_path = log_path


@dataclass(frozen=True)
class Outcome:
    task_id: str
    rounds: int
    committed: bool


def log_path(root: Path, task_id: str, round_: int, agent: str) -> Path:
    return root / ".whyline" / "relay" / "logs" / f"{task_id}-{round_}-{agent}.log"


def _run_agent(
    root: Path,
    settings: config.Config,
    agent: str,
    template_name: str,
    task: plan.Task,
    round_: int,
    review_feedback: str,
    echo: bool,
) -> Path:
    """Render the prompt, run the agent, and return the log path."""
    packet = whylinecmd.sync(root, task.task_id)
    prompt = prompts.render(
        prompts.load(root, template_name),
        task_id=task.task_id,
        task_text=task.text,
        sync_packet=packet,
        round_=round_,
        review_feedback=review_feedback,
    )
    target = log_path(root, task.task_id, round_, agent)
    try:
        agents.run(
            settings.agents[agent],
            prompt,
            cwd=root,
            log_path=target,
            timeout_seconds=settings.timeout_minutes * 60,
            echo=echo,
        )
    except agents.AgentTimeout as error:
        raise Paused(str(error), target) from error
    except agents.AgentMissing as error:
        raise Paused(str(error), target) from error
    return target


def _no_handoff_detail(target: Path) -> str:
    """Why an agent that handed nothing off probably stopped, for the human only.

    Never used to route. Claude's JSON result lists the commands it was denied;
    failing that, the last line the agent printed is usually the cause (for
    example a settings file that was not found).
    """
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[-1].startswith("{"):
        try:
            result = json.loads(lines[-1])
        except ValueError:
            result = None
        denials = result.get("permission_denials", []) if isinstance(result, dict) else []
        commands = [
            str(d.get("tool_input", {}).get("command", d.get("tool_name", "?")))[:60]
            for d in denials
            if isinstance(d, dict)
        ]
        if commands:
            return (
                f"; it was denied permission to run: {', '.join(commands)}. "
                "Check .whyline/relay/claude-settings.json"
            )
    if lines:
        last = "".join(ch for ch in lines[-1].strip() if ch.isprintable())[:160]
        return f'; its last output was: "{last}"'
    return ""


def _hit_a_limit(target: Path) -> bool:
    """Whether the agent's output says it ran out of quota.

    Only asked when the agent handed nothing off. Ordinary code and prose say
    "rate limit" and "too many requests" all the time, so those words alone must
    never discard a handoff the agent did make.
    """
    try:
        return agents.rate_limited(target.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False


def _approved(
    root: Path, task: plan.Task, base_commit: str, round_: int, log: Path | None
) -> Outcome:
    """An approval counts only when a real commit naming the task exists."""
    if not gitcheck.commit_verified(root, base_commit, task.task_id):
        raise Paused(
            f"{task.task_id} was approved but no commit naming it exists; "
            "the checkbox was not ticked",
            log,
        )
    return Outcome(task_id=task.task_id, rounds=round_, committed=True)


def run_task(
    root: Path,
    settings: config.Config,
    task: plan.Task,
    *,
    base_commit: str,
    echo: bool = True,
    resume: bool = False,
    start_round: int = 1,
    on_turn: Callable[[int, str | None], None] | None = None,
) -> Outcome:
    """Drive one task from implement to an approved, verified commit.

    Raises Paused for every stop condition. Never infers a verdict from an
    agent's exit code or output — only from the handoff record it wrote.

    With `resume`, re-enter at the decision point the handoff record shows,
    not at the start: a task Codex already handed off goes straight to the
    review, one sent back carries its feedback into Codex's next turn, and one
    already approved goes straight to commit verification. A record that names
    another task is ignored. `on_turn` is told the round and the last handoff
    id before every agent turn, so a caller can save resumable state.
    """
    round_ = start_round
    feedback = ""
    next_move = routing.IMPLEMENT
    gitcheck.ensure_relay_ignored(root)
    whylinecmd.claim(root, task.task_id, "codex", "implementer")
    if resume:
        current = handoff.read(root)
        if current is not None and current.task == task.task_id:
            resumed = routing.decide(current, None, settings.status_map)
            if resumed in (routing.REVIEW, routing.APPROVED):
                next_move = resumed
            elif (
                resumed == routing.IMPLEMENT
                and current.status == settings.status_map["changes"]
            ):
                feedback = current.summary

    while True:
        if next_move == routing.APPROVED:
            return _approved(root, task, base_commit, round_, None)
        agent = "codex" if next_move == routing.IMPLEMENT else "claude"
        template = "implement" if agent == "codex" else "review"
        previous = handoff.read(root)
        previous_id = previous.event_id if previous else None
        head_before = gitcheck.head_commit(root)
        if on_turn is not None:
            on_turn(round_, previous_id)

        target = _run_agent(
            root, settings, agent, template, task, round_, feedback, echo
        )
        if agent == "codex" and gitcheck.head_commit(root) != head_before:
            # Codex's sandbox does not stop it committing (measured on
            # codex-cli 0.155.1); only the prompt says not to, so check.
            raise Paused(
                "codex made a commit, which the relay forbids (only the reviewer "
                f"commits). Undo it with `git reset {head_before[:12]}` (your files "
                "stay), then resume",
                target,
            )

        record = handoff.read(root)
        move = routing.decide(record, previous_id, settings.status_map)

        if move == routing.NO_HANDOFF:
            if _hit_a_limit(target):
                raise Paused(
                    f"{agent} hit a usage or rate limit; try again when it resets",
                    target,
                )
            raise Paused(
                f"{agent} exited without handing off{_no_handoff_detail(target)}; "
                "nothing was routed",
                target,
            )
        if record.task != task.task_id:
            raise Paused(
                f"{agent} handed off for {record.task!r}, but this run is on "
                f"{task.task_id!r}; the relay will not guess",
                target,
            )
        if move == routing.BLOCKED:
            raise Paused(
                f"{agent} reported blocked: {record.summary or 'no summary given'}",
                target,
            )
        if move == routing.UNKNOWN:
            raise Paused(
                f"unrecognised handoff status {record.status!r}; the relay will not guess",
                target,
            )
        if move == routing.APPROVED:
            return _approved(root, task, base_commit, round_, target)
        if move == routing.IMPLEMENT:
            feedback = record.summary
            round_ += 1
            if round_ > settings.max_rounds:
                raise Paused(
                    f"{task.task_id} hit the {settings.max_rounds}-round cap without "
                    "an approval",
                    target,
                )
        next_move = move
