from whyline_relay import config, handoff, pipeline

STATUS = config.DEFAULTS["status_map"]


def record(**fields) -> handoff.Handoff:
    base = {"event_id": "e2", "task": "WL-1", "to_actor": "claude", "status": "ready-for-review", "summary": ""}
    return handoff.Handoff(**{**base, **fields})


LEGACY_AGENTS = {"implementer": "codex", "reviewer": "claude"}


def test_no_handoff_when_record_is_none():
    compiled = pipeline.compile_legacy(STATUS)
    assert pipeline.decide(None, None, compiled, LEGACY_AGENTS) == pipeline.Decision("no-handoff", None)


def test_no_handoff_when_event_id_is_unchanged():
    compiled = pipeline.compile_legacy(STATUS)
    assert pipeline.decide(record(event_id="e1"), "e1", compiled, LEGACY_AGENTS).kind == "no-handoff"


def test_ready_for_review_advances_to_the_review_stage():
    compiled = pipeline.compile_legacy(STATUS)
    assert pipeline.decide(record(), "e1", compiled, LEGACY_AGENTS) == pipeline.Decision("advance", "review")


def test_changes_requested_advances_to_the_implement_stage():
    compiled = pipeline.compile_legacy(STATUS)
    moved = record(to_actor="codex", status="changes-requested")
    assert pipeline.decide(moved, "e1", compiled, LEGACY_AGENTS) == pipeline.Decision("advance", "implement")


def test_approved_completes_regardless_of_recipient():
    compiled = pipeline.compile_legacy(STATUS)
    assert pipeline.decide(record(status="approved"), "e1", compiled, LEGACY_AGENTS) == pipeline.Decision("complete", None)


def test_blocked_short_circuits_regardless_of_recipient():
    compiled = pipeline.compile_legacy(STATUS)
    moved = record(to_actor="nobody-in-particular", status="blocked")
    assert pipeline.decide(moved, "e1", compiled, LEGACY_AGENTS) == pipeline.Decision("blocked", None)


def test_unrecognised_status_is_unknown():
    compiled = pipeline.compile_legacy(STATUS)
    assert pipeline.decide(record(status="banana"), "e1", compiled, LEGACY_AGENTS).kind == "unknown"


def test_a_handoff_addressed_to_the_wrong_agent_is_unknown():
    compiled = pipeline.compile_legacy(STATUS)
    moved = record(to_actor="codex", status="ready-for-review")
    assert pipeline.decide(moved, "e1", compiled, LEGACY_AGENTS).kind == "unknown"


def test_custom_status_map_is_honoured():
    custom = {**STATUS, "review": "needs-review"}
    compiled = pipeline.compile_legacy(custom)
    assert pipeline.decide(record(status="needs-review"), "e1", compiled, LEGACY_AGENTS) == pipeline.Decision("advance", "review")


def test_swapped_agent_names_route_by_name_not_by_the_default():
    compiled = pipeline.compile_legacy(STATUS)
    agents = {"implementer": "aider", "reviewer": "gemini"}
    ready = record(to_actor="gemini", status="ready-for-review")
    assert pipeline.decide(ready, "e1", compiled, agents) == pipeline.Decision("advance", "review")
    back = record(to_actor="aider", status="changes-requested")
    assert pipeline.decide(back, "e1", compiled, agents) == pipeline.Decision("advance", "implement")


def test_the_default_name_is_not_special_once_agents_change():
    compiled = pipeline.compile_legacy(STATUS)
    agents = {"implementer": "aider", "reviewer": "gemini"}
    ready = record(to_actor="claude", status="ready-for-review")
    assert pipeline.decide(ready, "e1", compiled, agents).kind == "unknown"


def test_one_agent_in_both_roles_routes_by_status_alone():
    compiled = pipeline.compile_legacy(STATUS)
    agents = {"implementer": "claude", "reviewer": "claude"}
    assert pipeline.decide(record(to_actor="claude", status="ready-for-review"), "e1", compiled, agents) == pipeline.Decision("advance", "review")
    assert pipeline.decide(record(to_actor="claude", status="changes-requested"), "e1", compiled, agents) == pipeline.Decision("advance", "implement")


# =============== genuine N-stage generality: not expressible in the old two-role system
def three_stage_pipeline() -> pipeline.Pipeline:
    return pipeline.Pipeline(
        roles={
            "implementer": pipeline.Role("implementer", agent="codex"),
            "tester": pipeline.Role("tester", agent="claude"),
            "reviewer": pipeline.Role("reviewer", agent="claude"),
        },
        stages={
            "draft": pipeline.Stage("draft", "implementer", "implement", {"ready": "test", "blocked-status": "@blocked"}),
            "test": pipeline.Stage("test", "tester", "test", {"passed": "review", "failed": "draft", "blocked-status": "@blocked"}),
            "review": pipeline.Stage("review", "reviewer", "review", {"approved": "@complete", "rejected": "draft", "blocked-status": "@blocked"}),
        },
        profiles={"full": pipeline.Profile("full", ("draft", "test", "review"))},
        default_profile="full",
    )


def test_three_stage_pipeline_advances_through_each_stage_in_turn():
    p = three_stage_pipeline()
    agents = {"implementer": "codex", "tester": "claude", "reviewer": "claude"}
    r1 = handoff.Handoff(event_id="e1", task="T", to_actor="claude", status="ready", summary="")
    assert pipeline.decide(r1, None, p, agents) == pipeline.Decision("advance", "test")
    r2 = handoff.Handoff(event_id="e2", task="T", to_actor="claude", status="passed", summary="")
    assert pipeline.decide(r2, "e1", p, agents) == pipeline.Decision("advance", "review")
    r3 = handoff.Handoff(event_id="e3", task="T", to_actor="claude", status="approved", summary="")
    assert pipeline.decide(r3, "e2", p, agents) == pipeline.Decision("complete", None)


def test_three_stage_pipeline_rejection_rewinds_to_a_named_stage_not_just_implement():
    p = three_stage_pipeline()
    agents = {"implementer": "codex", "tester": "claude", "reviewer": "claude"}
    failed = handoff.Handoff(event_id="e2", task="T", to_actor="codex", status="failed", summary="")
    assert pipeline.decide(failed, "e1", p, agents) == pipeline.Decision("advance", "draft")
    rejected = handoff.Handoff(event_id="e3", task="T", to_actor="codex", status="rejected", summary="")
    assert pipeline.decide(rejected, "e2", p, agents) == pipeline.Decision("advance", "draft")


def test_three_stage_pipeline_blocked_from_any_stage_short_circuits():
    p = three_stage_pipeline()
    agents = {"implementer": "codex", "tester": "claude", "reviewer": "claude"}
    r = handoff.Handoff(event_id="e1", task="T", to_actor="nobody", status="blocked-status", summary="")
    assert pipeline.decide(r, None, p, agents) == pipeline.Decision("blocked", None)


def test_three_stage_pipeline_wrong_recipient_is_unknown_not_a_guess():
    p = three_stage_pipeline()
    agents = {"implementer": "codex", "tester": "claude", "reviewer": "claude"}
    r = handoff.Handoff(event_id="e1", task="T", to_actor="codex", status="ready", summary="")
    assert pipeline.decide(r, None, p, agents).kind == "unknown"
