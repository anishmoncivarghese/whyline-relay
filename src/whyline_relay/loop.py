"""The state machine. The only module in the relay that decides anything."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from whyline_relay import (
    adapters,
    agents,
    config,
    failover,
    gitcheck,
    handoff,
    invocation,
    pipeline,
    plan,
    prompts,
    routing,
    running,
    state,
    whylinecmd,
)
from whyline_relay.adapters import bypass


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


def _blocked_reason(agent: str, record: handoff.Handoff) -> str:
    reason = f"{agent} reported blocked: {record.summary or 'no summary given'}"
    for question in record.questions:
        reason += f". Question: {question}"
    if len(reason) > 300:
        return reason[:297] + "..."
    return reason


def check_visit_cap(
    pipe: pipeline.Pipeline,
    stage_id: str,
    stage_visits: dict[str, int],
    task: plan.Task,
) -> None:
    cap = pipe.stages[stage_id].max_visits
    if stage_visits[stage_id] > cap:
        raise Paused(
            f"{task.task_id} hit stage {stage_id!r}'s {cap}-visit cap without "
            'reaching "@complete"',
            None,
        )


def log_path(
    root: Path, task_id: str, round_: int, agent: str, role: str = ""
) -> Path:
    suffix = f"-{role}" if role else ""
    return root / ".whyline" / "relay" / "logs" / (
        f"{task_id}-{round_}-{agent}{suffix}.log"
    )


def run_agent(
    root: Path,
    settings: config.Config,
    agent: str,
    role: str,
    template_name: str,
    task: plan.Task,
    round_: int,
    review_feedback: str,
    echo: bool,
    *,
    implementer: str,
    reviewer: str,
    action_label: str | None = None,
    log_suffix: str | None = None,
    actor: str = "",
    stage: str = "",
    profile: str = "",
    prompt_suffix: str = "",
    runner: failover.Runner = subprocess.run,
) -> Path:
    """Render the prompt, run the agent, and return the log path."""
    command = settings.agents[agent]
    adapter = config.adapter_for(settings, agent)
    found = bypass.find(adapter.name, command)
    if found:
        raise Paused(
            f"refusing to run {agent}: its command contains a permission-bypass "
            f"flag ({', '.join(found)}). The relay never runs an agent that way; "
            "remove it from .whyline/relay/config.toml",
            None,
        )
    running.start_turn(root, agent, task.task_id, round_, role=role)
    packet = whylinecmd.sync(root, task.task_id)
    prompt = prompts.render(
        prompts.load(root, template_name),
        task_id=task.task_id,
        task_text=task.text,
        sync_packet=packet,
        round_=round_,
        review_feedback=review_feedback,
        implementer=implementer,
        reviewer=reviewer,
        actor=actor,
        role=role,
        stage=stage,
        profile=profile,
    ) + prompt_suffix
    log_role = (
        log_suffix
        if log_suffix is not None
        else (role if implementer == reviewer else "")
    )
    target = log_path(root, task.task_id, round_, agent, log_role)
    action = action_label or (
        "implementing" if role == "implementer" else "reviewing"
    )
    if echo:
        agents.print_status(
            f"==> {agent}: {action} {task.task_id} "
            f"(round {round_} of {settings.max_rounds})"
        )
    started = time.monotonic()
    try:
        try:
            agents.run(
                command,
                prompt,
                cwd=root,
                log_path=target,
                timeout_seconds=settings.timeout_minutes * 60,
                echo=echo,
                agent_name=agent,
            )
        finally:
            if echo:
                agents.print_status(
                    f"<== {agent} finished in "
                    f"{agents.format_duration(time.monotonic() - started)}"
                )
    except agents.AgentTimeout as error:
        raise Paused(str(error), target) from error
    except agents.AgentMissing as error:
        raise Paused(str(error), target) from error
    return target


def _no_handoff_detail(target: Path, adapter: adapters.Adapter) -> str:
    """Why an agent that handed nothing off probably stopped, for the human only.

    Never used to route. The agent adapter interprets its own output format.
    """
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return adapter.diagnose(text)


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


def _commit_and_approve(
    root: Path,
    task: plan.Task,
    base_commit: str,
    round_: int,
    log: Path | None,
    summary: str,
) -> Outcome:
    """The relay itself makes a configured pipeline's one finishing commit (spec 5.6).

    No stage in a configured pipeline may commit -- the same HEAD-check every
    stage's turn is already held to also covers the terminal one, so this
    re-check is defense in depth, not the primary guard: if it ever fires, a
    stage ignored its own rendered prompt's protocol footer (prompts.stage_footer)
    and a bug let it through anyway.
    """
    if gitcheck.head_commit(root) != base_commit:
        raise Paused(
            f"{task.task_id}: HEAD moved before the relay could make its own commit; "
            f"a stage committed when it must not have. Undo it with `git reset "
            f"{base_commit[:12]}` (your files stay), then resume",
            log,
        )
    body = summary.strip() or f"chore: finish {task.task_id}"
    gitcheck.commit_all(root, f"{body} ({task.task_id})")
    return _approved(root, task, base_commit, round_, log)


def _run_task(
    root: Path,
    settings: config.Config,
    task: plan.Task,
    *,
    base_commit: str,
    echo: bool = True,
    resume: bool = False,
    start_round: int = 1,
    on_turn: Callable[[int, str | None, dict], None] | None = None,
    runner: failover.Runner = subprocess.run,
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
    implementer = failover.effective_agent(root, settings, "implementer")
    reviewer = failover.effective_agent(root, settings, "reviewer")
    gitcheck.ensure_relay_ignored(root)
    whylinecmd.claim(root, task.task_id, implementer, "implementer")
    if resume:
        current = handoff.read(root)
        if current is not None and current.task == task.task_id:
            resumed = routing.decide(
                current,
                None,
                settings.status_map,
                implementer=implementer,
                reviewer=reviewer,
            )
            if resumed in (routing.REVIEW, routing.APPROVED):
                next_move = resumed
            elif (
                resumed == routing.IMPLEMENT
                and current.status == settings.status_map["changes"]
            ):
                feedback = current.summary

    while True:
        implementer = failover.effective_agent(root, settings, "implementer")
        reviewer = failover.effective_agent(root, settings, "reviewer")
        if next_move == routing.APPROVED:
            return _approved(root, task, base_commit, round_, None)
        role = "implementer" if next_move == routing.IMPLEMENT else "reviewer"
        agent = implementer if role == "implementer" else reviewer
        template = "implement" if role == "implementer" else "review"
        previous = handoff.read(root)
        previous_id = previous.event_id if previous else None
        head_before = gitcheck.head_commit(root)
        if on_turn is not None:
            on_turn(round_, previous_id, {})

        target = run_agent(
            root,
            settings,
            agent,
            role,
            template,
            task,
            round_,
            feedback,
            echo,
            implementer=implementer,
            reviewer=reviewer,
            runner=runner,
        )
        if role == "implementer" and gitcheck.head_commit(root) != head_before:
            # Codex's sandbox does not stop it committing (measured on
            # codex-cli 0.155.1); only the prompt says not to, so check.
            raise Paused(
                f"{agent} made a commit, which the relay forbids (only the reviewer "
                f"commits). Undo it with `git reset {head_before[:12]}` (your files "
                "stay), then resume",
                target,
            )

        record = handoff.read(root)
        move = routing.decide(
            record,
            previous_id,
            settings.status_map,
            implementer=implementer,
            reviewer=reviewer,
        )

        if move == routing.NO_HANDOFF:
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            adapter = config.adapter_for(settings, agent)
            reason = failover.failover_reason(
                adapter, text, settings.agents[agent], runner=runner
            )
            if reason is not None:
                backup = settings.backups.get(role)
                if backup is not None and backup != agent:
                    verb, _ = failover.REASON_TEXT[reason]
                    failover.write_override(
                        root,
                        role,
                        failover.ActiveOverride(
                            agent=backup,
                            backup_for=agent,
                            reason=reason,
                            since=datetime.now().astimezone().isoformat(),
                        ),
                    )
                    if echo:
                        agents.print_status(
                            f"==> relay: {role} switched from {agent} to {backup} "
                            f"({agent} {verb})"
                        )
                    continue
                existing = failover.read_overrides(root).get(role)
                raise Paused(
                    failover.pause_message(agent, role, reason, existing), target
                )
            raise Paused(
                f"{agent} exited without handing off"
                f"{_no_handoff_detail(target, adapter)}; nothing was routed",
                target,
            )
        if record.task != task.task_id:
            raise Paused(
                f"{agent} handed off for {record.task!r}, but this run is on "
                f"{task.task_id!r}; the relay will not guess",
                target,
            )
        if record.from_actor and record.from_actor.strip().lower() != agent.lower():
            raise Paused(
                f"{agent} recorded its handoff as from {record.from_actor!r}, not {agent!r}. "
                "Check the prompt templates in .whyline/relay/prompts; after changing "
                f"[roles], run `{invocation.command('init')} --overwrite`",
                target,
            )
        if move == routing.BLOCKED:
            raise Paused(_blocked_reason(agent, record), target)
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


def _run_configured_task(
    root: Path,
    settings: config.Config,
    task: plan.Task,
    *,
    base_commit: str,
    echo: bool = True,
    resume: bool = False,
    start_round: int = 1,
    on_turn: Callable[[int, str | None, dict], None] | None = None,
    runner: failover.Runner = subprocess.run,
) -> Outcome:
    """Drive one task through a configured [pipeline], stage by stage.

    Mirrors _run_task's contract and most of its per-turn checks (task
    mismatch, from_actor, blocked, unknown, no-handoff/timeout) but routes on
    pipeline.decide() against the stage that just ran, not a fixed pair of
    roles. [roles.backup] is refused together with [pipeline] at config load
    (see config.py), so there is no failover branch here: a no-handoff always
    pauses -- there is never a backup to switch to.

    With `resume`, state.json's saved stage/profile/stage_visits pick the task
    back up where it paused. State is checkpointed (via `on_turn`) before every
    agent launch, never after: a crash before that checkpoint lands re-enters
    the same turn; a crash after it but before the agent's own handoff write
    also re-enters the same turn (the checkpoint never claims a turn happened
    until it's about to). A crash after the agent already wrote its handoff is
    caught here by re-deciding on whatever handoff already exists before the
    main loop restarts, so it is never re-run and never silently dropped.
    """
    pipe = settings.pipeline
    effective_agents = {name: role.agent for name, role in pipe.roles.items()}
    round_ = start_round
    feedback = ""
    gitcheck.ensure_relay_ignored(root)
    start_profile = task.profile or pipe.default_profile
    start_stage_id = pipe.profiles[start_profile].stages[0]
    whylinecmd.claim(
        root,
        task.task_id,
        pipe.roles[pipe.stages[start_stage_id].role].agent,
        pipe.stages[start_stage_id].role,
    )
    saved = state.load(root) if resume else None
    if resume and saved is not None and saved.task_id == task.task_id and saved.stage:
        if saved.pipeline_fingerprint != settings.pipeline_fingerprint:
            raise Paused(
                f"{task.task_id} was paused mid-pipeline, but the pipeline has changed "
                "since then; its saved stage and visit counts no longer match the "
                "configured graph. Resolve by hand before resuming",
                None,
            )
        if saved.stage == "@complete":
            # Approved and checkpointed before a previous pause, but not yet
            # committed: no agent to re-run, only the relay's own commit to retry.
            # The terminal handoff is still on disk -- nothing since has replaced
            # it, since committing it is the very last step of the task.
            current = handoff.read(root)
            if current is None or current.task != task.task_id:
                raise Paused(
                    f"{task.task_id} was approved before a previous pause, but its "
                    "handoff record is now missing or names a different task; the "
                    "relay will not guess the commit message. Resolve by hand",
                    None,
                )
            return _commit_and_approve(
                root, task, base_commit, round_, None, current.summary
            )
        profile_name = saved.profile
        current_stage_id = saved.stage
        stage_visits = dict(saved.stage_visits)
        current = handoff.read(root)
        if current is not None and current.task == task.task_id:
            resumed = pipeline.decide(
                current,
                None,
                pipe,
                effective_agents,
                current_stage_id=current_stage_id,
                profile_name=profile_name,
            )
            if resumed.kind == "advance":
                current_stage_id = resumed.target_stage
                stage_visits[current_stage_id] = (
                    stage_visits.get(current_stage_id, 0) + 1
                )
                check_visit_cap(pipe, current_stage_id, stage_visits, task)
                feedback = current.summary
            elif resumed.kind == "complete":
                return _commit_and_approve(
                    root, task, base_commit, round_, None, current.summary
                )
            elif resumed.kind == "blocked":
                raise Paused(
                    _blocked_reason(current.from_actor or "agent", current), None
                )
            # "unknown"/"no-handoff": current_stage_id/stage_visits stand; re-run it.
    else:
        profile_name = start_profile
        current_stage_id = start_stage_id
        stage_visits = {current_stage_id: 1}

    implementer_agent = (
        pipe.roles["implementer"].agent if "implementer" in pipe.roles else ""
    )
    reviewer_agent = pipe.roles["reviewer"].agent if "reviewer" in pipe.roles else ""
    while True:
        stage = pipe.stages[current_stage_id]
        agent = pipe.roles[stage.role].agent
        previous = handoff.read(root)
        previous_id = previous.event_id if previous else None
        head_before = gitcheck.head_commit(root)
        stage_state = {
            "profile": profile_name,
            "stage": current_stage_id,
            "stage_visits": dict(stage_visits),
            "pipeline_fingerprint": settings.pipeline_fingerprint,
        }
        if on_turn is not None:
            on_turn(round_, previous_id, stage_state)
        target = run_agent(
            root,
            settings,
            agent,
            stage.role,
            stage.prompt,
            task,
            round_,
            feedback,
            echo,
            implementer=implementer_agent,
            reviewer=reviewer_agent,
            action_label=stage.id,
            log_suffix=stage.id,
            actor=agent,
            stage=current_stage_id,
            profile=profile_name,
            prompt_suffix=prompts.stage_footer(
                stage, pipe, profile_name, effective_agents, agent, task.task_id
            ),
            runner=runner,
        )
        if gitcheck.head_commit(root) != head_before:
            raise Paused(
                f"{agent} made a commit in stage {current_stage_id!r}, which the relay "
                "forbids: for a configured pipeline, only the relay itself commits, "
                f"after every stage approves. Undo it with `git reset "
                f"{head_before[:12]}` (your files stay), then resume",
                target,
            )
        record = handoff.read(root)
        decision = pipeline.decide(
            record,
            previous_id,
            pipe,
            effective_agents,
            current_stage_id=current_stage_id,
            profile_name=profile_name,
        )
        if decision.kind == "no-handoff":
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            adapter = config.adapter_for(settings, agent)
            reason = failover.failover_reason(
                adapter, text, settings.agents[agent], runner=runner
            )
            if reason is not None:
                existing = failover.read_overrides(root).get(stage.role)
                raise Paused(
                    failover.pause_message(agent, stage.role, reason, existing), target
                )
            raise Paused(
                f"{agent} exited without handing off"
                f"{_no_handoff_detail(target, adapter)}; nothing was routed",
                target,
            )
        if record.task != task.task_id:
            raise Paused(
                f"{agent} handed off for {record.task!r}, but this run is on "
                f"{task.task_id!r}; the relay will not guess",
                target,
            )
        if record.from_actor and record.from_actor.strip().lower() != agent.lower():
            raise Paused(
                f"{agent} recorded its handoff as from {record.from_actor!r}, not "
                f"{agent!r}. Check the prompt templates in .whyline/relay/prompts; "
                f"after changing [pipeline] or [roles], run "
                f"`{invocation.command('init')} --overwrite`",
                target,
            )
        if decision.kind == "blocked":
            raise Paused(_blocked_reason(agent, record), target)
        if decision.kind == "unknown":
            raise Paused(
                f"unrecognised outcome {record.status!r} for stage "
                f"{current_stage_id!r}; the relay will not guess",
                target,
            )
        if decision.kind == "complete":
            # Checkpoint the approval before making the relay's own commit: a
            # crash between this save and the commit must not re-run any agent
            # on resume, only retry the commit (see the "@complete" branch above).
            if on_turn is not None:
                on_turn(
                    round_,
                    previous_id,
                    {
                        "profile": profile_name,
                        "stage": "@complete",
                        "stage_visits": dict(stage_visits),
                        "pipeline_fingerprint": settings.pipeline_fingerprint,
                    },
                )
            return _commit_and_approve(
                root, task, base_commit, round_, target, record.summary
            )
        feedback = record.summary
        current_stage_id = decision.target_stage
        stage_visits[current_stage_id] = stage_visits.get(current_stage_id, 0) + 1
        check_visit_cap(pipe, current_stage_id, stage_visits, task)
        round_ += 1
        if round_ > settings.max_rounds:
            raise Paused(
                f"{task.task_id} hit the {settings.max_rounds}-round cap without "
                'reaching "@complete"',
                target,
            )


def run_task(
    root: Path,
    settings: config.Config,
    task: plan.Task,
    *,
    base_commit: str,
    echo: bool = True,
    resume: bool = False,
    start_round: int = 1,
    on_turn: Callable[[int, str | None, dict], None] | None = None,
    _clear_running: bool = True,
    runner: failover.Runner = subprocess.run,
) -> Outcome:
    """Drive one task and clear its live marker when used outside ``run_plan``."""
    driver = _run_configured_task if settings.pipeline is not None else _run_task
    try:
        return driver(
            root,
            settings,
            task,
            base_commit=base_commit,
            echo=echo,
            resume=resume,
            start_round=start_round,
            on_turn=on_turn,
            runner=runner,
        )
    finally:
        if _clear_running:
            running.clear(root)


def stop_path(root: Path) -> Path:
    return config.relay_dir(root) / "STOP"


def stop_requested(root: Path) -> bool:
    return stop_path(root).exists()


def _save_pause(
    root: Path,
    plan_path: Path,
    branch: str,
    task: plan.Task,
    base_commit: str,
    reason: str,
    log: Path | None,
    only: str | None,
    progress: dict,
) -> None:
    stage_state = progress.get("stage_state") or {}
    state.save(
        root,
        state.RelayState(
            plan=str(plan_path),
            branch=branch,
            task_id=task.task_id,
            round=progress["round"],
            base_commit=base_commit,
            paused_reason=reason,
            log_path=str(log or ""),
            only=only or "",
            last_handoff_id=progress["last"] or "",
            profile=stage_state.get("profile", ""),
            stage=stage_state.get("stage", ""),
            stage_visits=stage_state.get("stage_visits", {}),
            pipeline_fingerprint=stage_state.get("pipeline_fingerprint", ""),
        ),
    )


def _require_clean(root: Path, plan_path: Path, task: plan.Task) -> None:
    """After an approval nothing may be left uncommitted, or it rides into the next task.

    The plan file is exempt: it is ticked and committed straight afterwards.
    """
    try:
        own = str(plan_path.resolve().relative_to(root.resolve()))
    except ValueError:
        own = None
    leftovers = [path for path in gitcheck.dirty_paths(root) if path != own]
    if leftovers:
        raise Paused(
            f"{task.task_id} was approved and committed, but files are still "
            f"uncommitted ({', '.join(leftovers[:5])}). Stash or remove them (do not "
            "commit them: the relay verifies the last commit's message), then resume, "
            "or resume with --allow-dirty."
        )


def _tick_and_commit(root: Path, plan_path: Path, task: plan.Task) -> None:
    """Tick the box and commit that one file, so the tree is clean after every approval.

    Re-reads the plan first: an agent may have edited it while it worked, and
    ticking a copy read before the run would silently overwrite that edit.
    """
    current = plan_path.read_text(encoding="utf-8")
    plan_path.write_text(plan.tick(current, task.task_id), encoding="utf-8")
    gitcheck.commit_paths(root, [plan_path], f"chore: tick {task.task_id} in the plan")


def _run_plan(
    root: Path,
    settings: config.Config,
    plan_path: Path,
    *,
    branch: str,
    only: str | None = None,
    resume: bool = False,
    allow_dirty: bool = False,
    echo: bool = True,
    runner: failover.Runner = subprocess.run,
) -> list[Outcome]:
    """Run every unchecked task in file order. Tick each only after it commits.

    With `resume`, the first task re-enters where it stopped: it reuses the base
    commit saved when it paused (the reviewer may already have committed, and a
    fresh base would make the approval unverifiable) and the round it was on,
    and `run_task` picks the next agent from the handoff record.
    """
    outcomes: list[Outcome] = []
    resuming = state.load(root) if resume else None
    while True:
        if stop_requested(root):
            print("STOP file present; not starting another task.")
            return outcomes
        tasks = plan.parse(plan_path.read_text(encoding="utf-8"))
        if only:
            task = plan.find(tasks, only)
            if task is None:
                raise plan.PlanError(f"no task {only!r} in the plan")
        else:
            task = plan.next_unchecked(tasks)
        if task is None or task.checked:
            state.clear(root)
            return outcomes

        mid_task = resuming is not None and resuming.task_id == task.task_id
        base_commit = resuming.base_commit if mid_task else gitcheck.head_commit(root)
        start_round = (resuming.round or 1) if mid_task else 1
        resuming = None
        progress = {"round": start_round, "last": None}

        def on_turn(round_: int, previous_id: str | None, stage_state: dict) -> None:
            progress["round"] = round_
            progress["last"] = previous_id
            progress["stage_state"] = stage_state

        try:
            outcome = run_task(
                root,
                settings,
                task,
                base_commit=base_commit,
                echo=echo,
                resume=mid_task,
                start_round=start_round,
                on_turn=on_turn,
                _clear_running=False,
                runner=runner,
            )
            if not allow_dirty:
                _require_clean(root, plan_path, task)
        except Paused as paused:
            _save_pause(
                root, plan_path, branch, task, base_commit,
                paused.reason, paused.log_path, only, progress,
            )
            raise
        except KeyboardInterrupt:
            _save_pause(
                root, plan_path, branch, task, base_commit,
                "interrupted by the user (Ctrl+C)", None, only, progress,
            )
            raise

        _tick_and_commit(root, plan_path, task)
        if echo:
            agents.print_status(f"==> relay: ticked {task.task_id} in the plan")
        outcomes.append(outcome)
        if only:
            state.clear(root)
            return outcomes


def run_plan(
    root: Path,
    settings: config.Config,
    plan_path: Path,
    *,
    branch: str,
    only: str | None = None,
    resume: bool = False,
    allow_dirty: bool = False,
    echo: bool = True,
    runner: failover.Runner = subprocess.run,
) -> list[Outcome]:
    """Run a plan while retaining one live marker across all of its tasks."""
    try:
        return _run_plan(
            root,
            settings,
            plan_path,
            branch=branch,
            only=only,
            resume=resume,
            allow_dirty=allow_dirty,
            echo=echo,
            runner=runner,
        )
    finally:
        running.clear(root)
