import pytest

from whyline_relay import config, handoff, routing

STATUS = config.DEFAULTS["status_map"]


def record(**fields) -> handoff.Handoff:
    base = {
        "event_id": "e2",
        "task": "WL-1",
        "to_actor": "claude",
        "status": "ready-for-review",
        "summary": "",
    }
    return handoff.Handoff(**{**base, **fields})


def test_unchanged_event_id_means_the_agent_never_handed_off():
    assert routing.decide(record(event_id="e1"), "e1", STATUS) == routing.NO_HANDOFF


def test_absent_handoff_is_also_no_handoff():
    assert routing.decide(None, None, STATUS) == routing.NO_HANDOFF


def test_ready_for_review_routes_to_claude():
    assert routing.decide(record(), "e1", STATUS) == routing.REVIEW


def test_changes_requested_routes_back_to_codex():
    moved = record(to_actor="codex", status="changes-requested")
    assert routing.decide(moved, "e1", STATUS) == routing.IMPLEMENT


def test_assigned_routes_to_codex():
    moved = record(to_actor="codex", status="assigned")
    assert routing.decide(moved, "e1", STATUS) == routing.IMPLEMENT


def test_approved_is_approved_regardless_of_recipient():
    assert routing.decide(record(status="approved"), "e1", STATUS) == routing.APPROVED


def test_blocked_is_blocked():
    assert routing.decide(record(status="blocked"), "e1", STATUS) == routing.BLOCKED


def test_unrecognised_status_is_unknown_not_a_guess():
    assert routing.decide(record(status="banana"), "e1", STATUS) == routing.UNKNOWN


def test_status_map_is_honoured():
    custom = {**STATUS, "review": "needs-review"}
    moved = record(status="needs-review")
    assert routing.decide(moved, "e1", custom) == routing.REVIEW


def test_roles_other_than_the_defaults_route_by_name():
    ready = record(to_actor="gemini", status="ready-for-review")
    assert (
        routing.decide(
            ready, "e1", STATUS, implementer="aider", reviewer="gemini"
        )
        == routing.REVIEW
    )
    back = record(to_actor="aider", status="changes-requested")
    assert (
        routing.decide(
            back, "e1", STATUS, implementer="aider", reviewer="gemini"
        )
        == routing.IMPLEMENT
    )


def test_the_default_reviewer_name_is_not_special_once_roles_change():
    ready = record(to_actor="claude", status="ready-for-review")
    assert (
        routing.decide(
            ready, "e1", STATUS, implementer="aider", reviewer="gemini"
        )
        == routing.UNKNOWN
    )


def test_one_agent_in_both_roles_routes_by_status():
    solo = dict(implementer="claude", reviewer="claude")
    assert (
        routing.decide(
            record(to_actor="claude", status="ready-for-review"),
            "e1",
            STATUS,
            **solo,
        )
        == routing.REVIEW
    )
    assert (
        routing.decide(
            record(to_actor="claude", status="changes-requested"),
            "e1",
            STATUS,
            **solo,
        )
        == routing.IMPLEMENT
    )


@pytest.mark.parametrize(
    ("to_actor", "status"),
    [
        ("codex", "ready-for-review"),
        ("claude", "changes-requested"),
        ("claude", "assigned"),
    ],
)
def test_a_handoff_addressed_to_the_wrong_agent_pauses(to_actor: str, status: str):
    """Spec 5.2 routes on (to_actor, status). Mis-addressed means a confused agent."""
    moved = record(to_actor=to_actor, status=status)
    assert routing.decide(moved, "e1", STATUS) == routing.UNKNOWN
