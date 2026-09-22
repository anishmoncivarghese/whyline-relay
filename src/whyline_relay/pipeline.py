"""The Role/Stage/Profile engine: a pure, agent-independent state machine.

routing.py's decide() is a thin, behavior-preserving wrapper over this module for
today's fixed two-role shape (compile_legacy). Nothing here is wired into loop.py
yet -- config.toml cannot configure a [pipeline] table, and no other module imports
this one except routing.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from whyline_relay import handoff


@dataclass(frozen=True)
class Role:
    name: str
    agent: str
    backup: str | None = None


@dataclass(frozen=True)
class Stage:
    id: str
    role: str  # a Role.name
    prompt: str  # which prompt template to render
    transitions: dict[str, str]  # outcome name -> a Stage.id, "@complete", or "@blocked"
    max_visits: int = 3


@dataclass(frozen=True)
class Profile:
    name: str
    stages: tuple[str, ...]


@dataclass(frozen=True)
class Pipeline:
    roles: dict[str, Role]
    stages: dict[str, Stage]
    profiles: dict[str, Profile]
    default_profile: str
    legacy: bool = False


@dataclass(frozen=True)
class Decision:
    kind: str  # "advance" | "complete" | "blocked" | "no-handoff" | "unknown"
    target_stage: str | None


def compile_legacy(status_map: dict[str, str]) -> Pipeline:
    """The pipeline today's fixed implementer/reviewer shape compiles to.

    One shared transition table on both stages reproduces routing.py's actual
    statelessness: today's decide() never knows or cares which agent's turn just
    ended, only (status, to_actor). Splitting this into two different per-stage
    tables would be a behavior change, not a refactor.
    """
    shared = {
        status_map["review"]: "review",
        status_map["changes"]: "implement",
        status_map["assigned"]: "implement",
        status_map["approved"]: "@complete",
        status_map["blocked"]: "@blocked",
    }
    return Pipeline(
        roles={
            "implementer": Role(name="implementer", agent=""),
            "reviewer": Role(name="reviewer", agent=""),
        },
        stages={
            "implement": Stage(
                id="implement",
                role="implementer",
                prompt="implement",
                transitions=dict(shared),
            ),
            "review": Stage(
                id="review",
                role="reviewer",
                prompt="review",
                transitions=dict(shared),
            ),
        },
        profiles={"default": Profile(name="default", stages=("implement", "review"))},
        default_profile="default",
        legacy=True,
    )


def decide(
    record: handoff.Handoff | None,
    previous_id: str | None,
    pipeline: Pipeline,
    effective_agents: dict[str, str],
) -> Decision:
    """Pick the next move from the handoff record alone.

    Checks every stage's transitions for one that accepts this status;
    "@complete" and "@blocked" apply regardless of recipient (matching today's
    approved/blocked), a real stage target requires the handoff be addressed to
    whoever fills that stage's role. Never guesses: no match anywhere is
    "unknown", not a default.
    """
    if record is None or (previous_id is not None and record.event_id == previous_id):
        return Decision("no-handoff", None)
    for stage in pipeline.stages.values():
        target = stage.transitions.get(record.status)
        if target is None:
            continue
        if target == "@blocked":
            return Decision("blocked", None)
        if target == "@complete":
            return Decision("complete", None)
        target_stage = pipeline.stages[target]
        if record.to_actor == effective_agents.get(target_stage.role):
            return Decision("advance", target)
    return Decision("unknown", None)
