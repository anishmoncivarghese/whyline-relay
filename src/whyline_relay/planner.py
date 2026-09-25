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

import subprocess
from pathlib import Path

from whyline_relay import config, failover, gitcheck, handoff
from whyline_relay import pipeline as pipeline_module
from whyline_relay import loop, plan, prompts, state, whylinecmd


PLAN_TASK_ID = "__plan__"


class PlanAlreadyInProgress(RuntimeError):
    """A plan session is already checkpointed; resume or discard it first."""


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
                raise loop.Paused(_blocked_reason(previous), None)
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
            raise loop.Paused(_blocked_reason(record), target)
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
