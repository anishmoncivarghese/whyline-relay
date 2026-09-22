"""The routing table: a thin, behavior-preserving wrapper over the pipeline engine."""
from __future__ import annotations
from whyline_relay import handoff, pipeline
IMPLEMENT = "implement"
REVIEW = "review"
APPROVED = "approved"
BLOCKED = "blocked"
NO_HANDOFF = "no-handoff"
UNKNOWN = "unknown"
IMPLEMENTER = "codex"
REVIEWER = "claude"
_KIND_TO_MOVE = {
    "no-handoff": NO_HANDOFF,
    "blocked": BLOCKED,
    "complete": APPROVED,
    "unknown": UNKNOWN,
}
_STAGE_TO_MOVE = {"implement": IMPLEMENT, "review": REVIEW}
def decide(
    record: handoff.Handoff | None,
    previous_id: str | None,
    status_map: dict[str, str],
    implementer: str = IMPLEMENTER,
    reviewer: str = REVIEWER,
) -> str:
    """Pick the next move from the handoff record alone.
    A missing record, or one whose event id has not changed since the agent
    started, means the agent exited without handing off. That is never inferred
    to be success: the caller pauses.
    Compiles today's fixed implementer/reviewer shape into a pipeline.Pipeline
    and delegates to pipeline.decide(); see pipeline.compile_legacy for why one
    shared transition table on both stages is what reproduces this function's
    actual behavior (it has never depended on which agent's turn just ended).
    """
    compiled = pipeline.compile_legacy(status_map)
    decision = pipeline.decide(
        record,
        previous_id,
        compiled,
        effective_agents={"implementer": implementer, "reviewer": reviewer},
    )
    if decision.kind == "advance":
        return _STAGE_TO_MOVE[decision.target_stage]
    return _KIND_TO_MOVE[decision.kind]
