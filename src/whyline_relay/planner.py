"""The planner: drafts a plan.md from a free-text description via its own small
draft<->review pipeline, then a human approval gate.

Reuses pipeline.py's Role/Stage/Profile/Pipeline engine and loop.py's run_agent/
check_visit_cap -- shared, not duplicated, since both were already shared
between _run_task and _run_configured_task before this module became a third
caller. Never touches git, never ticks plan.md: nothing worth committing exists
until a human approves at this module's own gate (added in Task 6, below).

Spec: docs/superpowers/specs/2026-09-25-relay-planner-workflow.md.
"""

from __future__ import annotations

from collections.abc import Sequence
import subprocess
from pathlib import Path

from whyline_relay import config, failover, gitcheck, handoff, init, invocation
from whyline_relay import pipeline as pipeline_module
from whyline_relay import plan, prompts, state, whylinecmd
from whyline_relay import loop


PLAN_TASK_ID = "__plan__"


class PlanAlreadyInProgress(RuntimeError):
    """A plan session is already checkpointed; resume or discard it first."""


class PlanExists(RuntimeError):
    """plan.md already exists and the caller did not ask to replace it."""


def validate(text: str) -> list[str]:
    """Problems that stop `text` being used as a plan; empty when it's fine.

    Prose with no checkboxes parses as an empty list, so that is a problem
    too.
    """
    try:
        tasks = plan.parse(text)
    except plan.PlanError as error:
        return [str(error)]
    if not tasks:
        return ["no tasks found -- write each task as `- [ ] ID: title`"]
    return []


def approve(
    root: Path,
    settings: config.Config,
    draft_path: Path,
    *,
    drafted_by: str,
    replace: bool = False,
    clear_checkpoint: bool = False,
    target: Path | None = None,
) -> Path:
    """Writes the draft as the plan and commits only that file. Raises
    plan.PlanError for a draft that isn't a usable plan, and PlanExists when
    a plan is already there and `replace` is false."""
    text = draft_path.read_text(encoding="utf-8")
    problems = validate(text)
    if problems:
        raise plan.PlanError("; ".join(problems))
    named = target is not None
    target = target if named else root / settings.plan
    if target.exists() and not replace:
        raise PlanExists(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    if named:
        shown = target.relative_to(root).as_posix() if target.is_relative_to(root) else str(target)
        message = f"docs: add plan {shown} drafted by {drafted_by}"
    else:
        message = f"docs: add plan drafted by {drafted_by}"
    gitcheck.commit_paths(root, [target], message)
    if clear_checkpoint:
        state.clear_plan(root)
    return target


def draft_path(root: Path) -> Path:
    return config.relay_dir(root) / "draft-plan.md"


def _pipeline_for(settings: config.Config) -> pipeline_module.Pipeline:
    cfg = settings.planner
    return pipeline_module.Pipeline(
        roles={
            "draft": pipeline_module.Role(name="draft", agent=cfg.draft),
            "review": pipeline_module.Role(name="review", agent=cfg.review),
        },
        stages={
            "draft": pipeline_module.Stage(
                id="draft",
                role="draft",
                prompt="plan-draft",
                transitions={"ready": "@next", "blocked": "@blocked"},
                max_visits=cfg.max_visits,
            ),
            "review": pipeline_module.Stage(
                id="review",
                role="review",
                prompt="plan-review",
                transitions={
                    "approved": "@complete",
                    "revise": "draft",
                    "blocked": "@blocked",
                },
                max_visits=cfg.max_visits,
            ),
        },
        profiles={
            "default": pipeline_module.Profile("default", ("draft", "review"))
        },
        default_profile="default",
    )


def _task_for(description: str) -> plan.Task:
    return plan.Task(
        task_id=PLAN_TASK_ID, text=description, checked=False, line_index=0
    )


def _blocked_reason(record: handoff.Handoff) -> str:
    reason = f"the plan draft was blocked: {record.summary or 'no summary given'}"
    for question in record.questions:
        reason += f". Question: {question}"
    return reason


def _checkpoint(
    root: Path,
    description: str,
    stage: str,
    round_: int,
    stage_visits: dict[str, int],
    feedback: str,
) -> None:
    state.save_plan(
        root,
        state.PlanState(
            description=description,
            stage=stage,
            round=round_,
            stage_visits=dict(stage_visits),
            agent="",
            feedback=feedback,
            draft_path=str(draft_path(root)),
            paused_reason="",
            log_path="",
        ),
    )


def _run_pipeline(
    root: Path,
    settings: config.Config,
    description: str,
    *,
    current_stage_id: str = "draft",
    round_: int = 1,
    stage_visits: dict[str, int] | None = None,
    feedback: str = "",
    consult_handoff: bool = False,
    echo: bool = True,
    runner: failover.Runner = subprocess.run,
    on_stage=None,
) -> None:
    """Drive the draft<->review loop until "@complete" is checkpointed.

    Raises loop.Paused for every stop condition -- no-handoff, an unrecognised
    outcome, either stage's own "blocked", or a stage's max_visits exceeded --
    exactly like _run_configured_task. blocked is always a Paused exception
    here too, never a graceful in-process branch (spec 5.6).

    consult_handoff=True is a genuine crash-resume: the last turn may already
    have written its handoff before the process died, so the saved stage is
    re-decided against whatever handoff exists before launching anything new.
    consult_handoff=False (the default) always starts a fresh turn at
    current_stage_id -- used both for a brand-new session and for a
    human-initiated "request changes" round, which must not reuse a stale
    handoff left over from the review stage's own prior "approved".
    """
    pipe = _pipeline_for(settings)
    effective_agents = {name: role.agent for name, role in pipe.roles.items()}
    task = _task_for(description)
    gitcheck.ensure_relay_ignored(root)
    # Planning runs before Set up/init, so no preflight has checked for the
    # files claude's managed command names. Generated, never committed:
    # planning commits only plan.md.
    for name in effective_agents.values():
        init.ensure_permission_files(root, config.adapter_for(settings, name).name)
    stage_visits = dict(stage_visits) if stage_visits else {current_stage_id: round_}

    if consult_handoff:
        previous = handoff.read(root)
        if previous is not None and previous.task == task.task_id:
            decision = pipeline_module.decide(
                previous,
                None,
                pipe,
                effective_agents,
                current_stage_id=current_stage_id,
                profile_name="default",
            )
            if decision.kind == "advance":
                current_stage_id = decision.target_stage
                stage_visits[current_stage_id] = (
                    stage_visits.get(current_stage_id, 0) + 1
                )
                loop.check_visit_cap(pipe, current_stage_id, stage_visits, task)
                feedback = previous.summary
            elif decision.kind == "complete":
                _checkpoint(
                    root, description, "@complete", round_, stage_visits, feedback
                )
                return
            elif decision.kind == "blocked":
                stage_agent = pipe.roles[pipe.stages[current_stage_id].role].agent
                raise _blocked(previous, current_stage_id, stage_agent, None)
            # "unknown"/"no-handoff": current_stage_id/feedback stand; re-run it.

    while True:
        stage = pipe.stages[current_stage_id]
        agent = pipe.roles[stage.role].agent
        previous = handoff.read(root)
        previous_id = previous.event_id if previous else None
        whylinecmd.claim(root, task.task_id, agent, stage.role)
        _checkpoint(
            root, description, current_stage_id, round_, stage_visits, feedback
        )
        if on_stage is not None:
            on_stage(stage.id, agent)
        target = loop.run_agent(
            root,
            settings,
            agent,
            stage.role,
            stage.prompt,
            task,
            round_,
            feedback,
            echo,
            implementer="",
            reviewer="",
            action_label=stage.id,
            log_suffix=stage.id,
            actor=agent,
            stage=current_stage_id,
            profile="default",
            prompt_suffix=prompts.stage_footer(
                stage, pipe, "default", effective_agents, agent, task.task_id
            ),
            runner=runner,
        )
        record = handoff.read(root)
        decision = pipeline_module.decide(
            record,
            previous_id,
            pipe,
            effective_agents,
            current_stage_id=current_stage_id,
            profile_name="default",
        )
        if decision.kind == "no-handoff":
            raise loop.Paused(
                f"{agent} exited without handing off; nothing was routed", target
            )
        if record.task != task.task_id:
            raise loop.Paused(
                f"{agent} handed off for {record.task!r}, but this plan session uses "
                f"{task.task_id!r}; the relay will not guess",
                target,
            )
        if decision.kind == "blocked":
            raise _blocked(record, current_stage_id, agent, target)
        if decision.kind == "unknown":
            raise loop.Paused(
                f"unrecognised outcome {record.status!r} for stage "
                f"{current_stage_id!r}; the relay will not guess",
                target,
            )
        if decision.kind == "complete":
            _checkpoint(root, description, "@complete", round_, stage_visits, feedback)
            return
        feedback = record.summary
        current_stage_id = decision.target_stage
        round_ += 1
        stage_visits[current_stage_id] = stage_visits.get(current_stage_id, 0) + 1
        loop.check_visit_cap(pipe, current_stage_id, stage_visits, task)


def start(
    root: Path,
    settings: config.Config,
    description: str,
    *,
    echo: bool = True,
    confirm=input,
    runner: failover.Runner = subprocess.run,
) -> str:
    """Start a brand-new plan session: draft, auto-review, then the human gate."""
    if state.load_plan(root) is not None:
        raise PlanAlreadyInProgress(
            "a plan draft is already in progress; run "
            f"`{invocation.command('resume')}` or `{invocation.command('plan --discard')}`"
        )
    _run_pipeline(root, settings, description, echo=echo, runner=runner)
    return _human_gate(
        root, settings, description, confirm=confirm, echo=echo, runner=runner
    )


def resume(
    root: Path,
    settings: config.Config,
    saved: state.PlanState,
    *,
    echo: bool = True,
    confirm=input,
    runner: failover.Runner = subprocess.run,
) -> str:
    """Resume an in-flight plan session from its checkpoint."""
    if saved.stage != "@complete":
        _run_pipeline(
            root,
            settings,
            saved.description,
            current_stage_id=saved.stage,
            round_=saved.round,
            stage_visits=saved.stage_visits,
            feedback=saved.feedback,
            consult_handoff=True,
            echo=echo,
            runner=runner,
        )
    return _human_gate(
        root, settings, saved.description, confirm=confirm, echo=echo, runner=runner
    )


def discard(root: Path) -> str:
    saved = state.load_plan(root)
    if saved is None:
        return "Nothing to discard."
    state.clear_plan(root)
    return f"Discarded. The draft is still at {saved.draft_path}, if you want it."


class NoPlanInProgress(RuntimeError):
    """There is no checkpointed plan draft to revise or resume."""


class PlanQuestions(loop.Paused):
    """A plan stage stopped to ask a person something. The checkpoint keeps
    the stage, so answer() re-runs exactly that stage."""

    def __init__(self, reason, log_path, *, questions, stage: str, agent: str):
        super().__init__(reason, log_path)
        self.questions = tuple(questions)
        self.stage = stage
        self.agent = agent


def _blocked(record: handoff.Handoff, stage: str, agent: str, target) -> loop.Paused:
    if record.questions:
        return PlanQuestions(
            _blocked_reason(record), target,
            questions=record.questions, stage=stage, agent=agent,
        )
    return loop.Paused(_blocked_reason(record), target)


def answer_feedback(questions: Sequence[str], answers: str) -> str:
    if not questions:
        return f"The human answered your questions:\n{answers.strip()}"
    asked = "\n".join(f"{number}. {q}" for number, q in enumerate(questions, 1))
    return f"You asked:\n{asked}\nThe human answered:\n{answers.strip()}"


_STAGE_WORDS = {"draft": "drafting the plan", "review": "reviewing the draft"}


def _announcer(print_fn):
    if print_fn is None:
        return None
    return lambda stage_id, agent: print_fn(
        f"{agent} is {_STAGE_WORDS.get(stage_id, stage_id)}"
    )


def pending_description(root: Path) -> str | None:
    """What the checkpointed draft was asked to build, or None."""
    saved = state.load_plan(root)
    return saved.description if saved is not None else None


def draft(
    root: Path,
    settings: config.Config,
    description: str,
    *,
    print_fn=None,
    runner: failover.Runner = subprocess.run,
) -> Path:
    """Runs the draft<->review pipeline with no terminal gate and returns the
    draft's path. The checkpoint stays until approve(clear_checkpoint=True)
    or discard()."""
    if state.load_plan(root) is not None:
        raise PlanAlreadyInProgress(
            "a plan draft is already in progress; resume it or discard it first"
        )
    _run_pipeline(
        root,
        settings,
        description,
        echo=False,
        runner=runner,
        on_stage=_announcer(print_fn),
    )
    return draft_path(root)


def revise(
    root: Path,
    settings: config.Config,
    feedback: str,
    *,
    print_fn=None,
    runner: failover.Runner = subprocess.run,
) -> Path:
    """Re-drafts with a human's requested change, then re-reviews."""
    saved = state.load_plan(root)
    if saved is None:
        raise NoPlanInProgress("no plan draft is in progress")
    _run_pipeline(
        root,
        settings,
        saved.description,
        current_stage_id="draft",
        round_=1,
        stage_visits={"draft": 1},
        feedback=feedback,
        echo=False,
        runner=runner,
        on_stage=_announcer(print_fn),
    )
    return draft_path(root)


def resume_draft(
    root: Path,
    settings: config.Config,
    *,
    print_fn=None,
    runner: failover.Runner = subprocess.run,
) -> Path:
    """Finishes a checkpointed draft (a crash, or a closed console) without
    the terminal gate."""
    saved = state.load_plan(root)
    if saved is None:
        raise NoPlanInProgress("no plan draft is in progress")
    if saved.stage != "@complete":
        _run_pipeline(
            root,
            settings,
            saved.description,
            current_stage_id=saved.stage,
            round_=saved.round,
            stage_visits=saved.stage_visits,
            feedback=saved.feedback,
            consult_handoff=True,
            echo=False,
            runner=runner,
            on_stage=_announcer(print_fn),
        )
    return draft_path(root)


def answer(
    root: Path,
    settings: config.Config,
    answers: str,
    *,
    print_fn=None,
    runner: failover.Runner = subprocess.run,
) -> Path:
    """Re-runs the stage that asked, with the person's answers as its
    feedback. Raises PlanQuestions again if it still needs something."""
    saved = state.load_plan(root)
    if saved is None:
        raise NoPlanInProgress("no plan draft is in progress")
    record = handoff.read(root)
    asked = record.questions if record is not None and record.task == PLAN_TASK_ID else ()
    _run_pipeline(
        root,
        settings,
        saved.description,
        current_stage_id=saved.stage,
        round_=saved.round,
        stage_visits=saved.stage_visits,
        feedback=answer_feedback(asked, answers),
        echo=False,
        runner=runner,
        on_stage=_announcer(print_fn),
    )
    return draft_path(root)


def review_gate(
    root: Path,
    settings: config.Config,
    draft_path: Path,
    description: str,
    *,
    drafted_by: str,
    revise_fn,
    on_settled=lambda: None,
    confirm=input,
    echo: bool = True,
    runner: failover.Runner = subprocess.run,
) -> str:
    """The shared three-way human approval gate for any drafted plan.md.

    `revise_fn(feedback)` is called on "request changes" and must leave a
    revised draft at `draft_path` when it returns -- the gate then re-reads
    it and asks again. `on_settled()` is called once, right after a discard
    or right after a successful approve-write, so a caller with its own
    session state (the simple planner's checkpoint; a future caller's own
    equivalent) can clear it -- callers with none pass the default no-op.
    """
    text = draft_path.read_text(encoding="utf-8")
    print(text)
    try:
        answer = confirm("Approve, [r]equest changes, or [d]iscard? [A/r/d] ").strip().lower()
    except EOFError:
        answer = "d"
    if answer.startswith("r"):
        try:
            feedback = confirm("What should change? ").strip()
        except EOFError:
            feedback = ""
        revise_fn(feedback)
        return review_gate(
            root, settings, draft_path, description,
            drafted_by=drafted_by, revise_fn=revise_fn, on_settled=on_settled,
            confirm=confirm, echo=echo, runner=runner,
        )
    if answer.startswith("d"):
        on_settled()
        return f"Discarded. The draft is still at {draft_path}, if you want it."
    target = root / settings.plan
    replace = False
    if target.exists():
        try:
            overwrite = confirm(f"{target} already exists. Replace it? [y/N] ").strip().lower()
        except EOFError:
            overwrite = "n"
        if not overwrite.startswith("y"):
            return f"Not approved: {target} already exists and was not replaced."
        replace = True
    try:
        approve(root, settings, draft_path, drafted_by=drafted_by, replace=replace)
    except plan.PlanError as error:
        return f"Not approved: {error}. The draft is still at {draft_path}."
    on_settled()
    try:
        start_now = confirm("Start whyline-relay on this plan now? [y/N] ").strip().lower()
    except EOFError:
        start_now = "n"
    if not start_now.startswith("y"):
        return f"Wrote {target}. Run `{invocation.command('start')}` when ready."
    branch = f"{settings.branch_prefix}{target.stem}"
    gitcheck.ensure_branch(root, branch)
    outcomes = loop.run_plan(root, settings, target, branch=branch, only=None)
    return f"Plan complete: {len(outcomes)} task(s) approved and committed."


def _human_gate(
    root: Path,
    settings: config.Config,
    description: str,
    *,
    confirm=input,
    echo: bool = True,
    runner: failover.Runner = subprocess.run,
) -> str:
    """The simple planner's own gate -- a thin wrapper over review_gate,
    preserving exact prior behavior (revise re-runs the draft<->review
    pipeline; settling clears this module's own checkpoint)."""

    def revise(feedback: str) -> None:
        _run_pipeline(
            root, settings, description,
            current_stage_id="draft", round_=1, stage_visits={"draft": 1},
            feedback=feedback, echo=echo, runner=runner,
        )

    return review_gate(
        root, settings, draft_path(root), description,
        drafted_by=settings.planner.draft,
        revise_fn=revise,
        on_settled=lambda: state.clear_plan(root),
        confirm=confirm, echo=echo, runner=runner,
    )
