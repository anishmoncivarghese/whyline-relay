from whyline_relay import agents, brainstorm


def _record(response="", raw=""):
    return {"ok": False, "response": response, "raw": raw}


def test_claudes_limit_wording_is_a_rate_limit():
    assert agents.rate_limited("You've hit your limit · resets 3pm")
    reason = brainstorm.failure_reason(record=_record(
        raw='{"type":"result","is_error":true,"result":"You\'ve hit your limit · resets 3pm"}'
    ))
    assert reason.startswith("quota/rate-limit — ")
    assert "hit your limit" in reason


def test_an_unknown_failure_shows_the_agents_last_line():
    reason = brainstorm.failure_reason(record=_record(raw="starting\nError: model not found\n"))
    assert reason == "generic non-zero failure — Error: model not found"


def test_the_detail_is_one_printable_line_of_at_most_160_characters():
    reason = brainstorm.failure_reason(record=_record(raw="x" * 5000 + "\x1b[31m\x07"))
    detail = reason.split(" — ", 1)[1]
    assert len(detail) <= 160 and detail.isprintable()


def test_no_output_gives_the_category_alone():
    assert brainstorm.failure_reason(record=_record()) == "generic non-zero failure"


def test_an_exception_keeps_its_message():
    reason = brainstorm.failure_reason(error=RuntimeError("boom: socket closed"))
    assert reason == "generic non-zero failure — boom: socket closed"
