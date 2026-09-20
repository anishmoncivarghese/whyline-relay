"""The routing table, as a pure function. Every relay decision passes through here."""

from __future__ import annotations

from whyline_relay import handoff

IMPLEMENT = "implement"
REVIEW = "review"
APPROVED = "approved"
BLOCKED = "blocked"
NO_HANDOFF = "no-handoff"
UNKNOWN = "unknown"

IMPLEMENTER = "codex"
REVIEWER = "claude"


def decide(
    record: handoff.Handoff | None,
    previous_id: str | None,
    status_map: dict[str, str],
) -> str:
    """Pick the next move from the handoff record alone.

    A missing record, or one whose event id has not changed since the agent
    started, means the agent exited without handing off. That is never inferred
    to be success: the caller pauses.

    Routing is on (to_actor, status), as the spec's table has it. A handoff
    addressed to the wrong agent for its status, such as ready-for-review sent
    to the implementer, means a confused agent, so it is UNKNOWN and the caller
    pauses. Only approved and blocked apply whoever the recipient is.
    """
    if record is None or (previous_id is not None and record.event_id == previous_id):
        return NO_HANDOFF
    status = record.status
    if status == status_map["approved"]:
        return APPROVED
    if status == status_map["blocked"]:
        return BLOCKED
    if status == status_map["review"] and record.to_actor == REVIEWER:
        return REVIEW
    if (
        status in (status_map["changes"], status_map["assigned"])
        and record.to_actor == IMPLEMENTER
    ):
        return IMPLEMENT
    return UNKNOWN
