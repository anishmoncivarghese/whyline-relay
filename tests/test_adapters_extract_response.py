import json

from whyline_relay import adapters
from whyline_relay.adapters import claude as claude_adapter
from whyline_relay.adapters import codex as codex_adapter


def test_claude_extracts_the_result_field():
    captured = (
        '{"type":"result","subtype":"success","is_error":false,'
        '"result":"pong","permission_denials":[]}\n'
    )
    assert claude_adapter.ADAPTER.extract_response(captured) == "pong"


def test_claude_falls_back_to_last_line_detail_on_malformed_json():
    captured = "not json at all\n"
    assert claude_adapter.ADAPTER.extract_response(captured) == "not json at all"


def test_claude_does_not_use_an_output_file():
    assert claude_adapter.ADAPTER.uses_output_file is False


def test_codex_strips_whitespace_from_the_output_file_contents():
    assert codex_adapter.ADAPTER.extract_response("  pong\n") == "pong"


def test_codex_uses_an_output_file():
    assert codex_adapter.ADAPTER.uses_output_file is True


def test_generic_tries_result_then_response_then_text_then_message():
    assert adapters.GENERIC.extract_response('{"result":"a"}') == "a"
    assert adapters.GENERIC.extract_response('{"response":"b"}') == "b"
    assert adapters.GENERIC.extract_response('{"text":"c"}') == "c"
    assert adapters.GENERIC.extract_response('{"message":"d"}') == "d"


def test_generic_verified_agy_and_grok_shapes():
    # Exact shapes captured from real `agy`/`grok` invocations this session.
    grok_output = json.dumps({"text": "pong", "stopReason": "end_turn"})
    agy_output = json.dumps({"status": "SUCCESS", "response": "pong\n"})
    assert adapters.GENERIC.extract_response(grok_output) == "pong"
    assert adapters.GENERIC.extract_response(agy_output).strip() == "pong"


def test_generic_extracts_a_pretty_printed_grok_result():
    captured = json.dumps(
        {"text": "pong", "stopReason": "end_turn", "thought": "done"},
        indent=2,
    )
    assert adapters.GENERIC.extract_response(captured) == "pong"


def test_generic_falls_back_to_last_line_detail_when_no_known_field():
    captured = '{"unrelated_field": "x"}\n'
    assert adapters.GENERIC.extract_response(captured) == '{"unrelated_field": "x"}'


def test_generic_does_not_use_an_output_file():
    assert adapters.GENERIC.uses_output_file is False
