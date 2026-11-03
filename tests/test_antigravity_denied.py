import json
from pathlib import Path

from whyline_relay import loop
from whyline_relay.adapters import antigravity, claude, generic

COMMAND = ["agy", "--output-format", "json", "-p"]
DENIED = json.dumps(
    {
        "status": "SUCCESS",
        "denied_actions": [
            {"action": "command", "display_name": "RunCommand"},
            {"action": "command", "display_name": "RunCommand"},
        ],
    },
    indent=2,
)
CLEAN = json.dumps({"status": "SUCCESS", "denied_actions": []})


def test_an_agy_result_with_denied_actions_names_the_tool_types():
    detail = antigravity.denied_detail(generic.ADAPTER, DENIED, COMMAND)
    assert detail == (
        "antigravity refused 2 tool call(s) (RunCommand, RunCommand). agy names "
        "only the tool type, not the command; add what it needs to the "
        "--settings allow list in [agents.antigravity], or run it yourself."
    )


def test_an_agy_path_is_recognised_by_its_name():
    assert antigravity.denied_detail(generic.ADAPTER, DENIED, ["/opt/bin/agy", "-p"])


def test_an_agy_result_without_denied_actions_gives_nothing():
    assert antigravity.denied_detail(generic.ADAPTER, CLEAN, COMMAND) == ""
    no_key = json.dumps({"status": "SUCCESS"})
    assert antigravity.denied_detail(generic.ADAPTER, no_key, COMMAND) == ""
    assert antigravity.denied_detail(generic.ADAPTER, "not json", COMMAND) == ""


def test_a_non_agy_generic_agent_gives_nothing():
    assert antigravity.denied_detail(generic.ADAPTER, DENIED, ["grok", "-p"]) == ""
    assert antigravity.denied_detail(generic.ADAPTER, DENIED, []) == ""


def test_a_built_in_adapter_gives_nothing():
    assert antigravity.denied_detail(claude.ADAPTER, DENIED, COMMAND) == ""


def test_the_pause_detail_reports_the_refusal(tmp_path: Path):
    target = tmp_path / "out.log"
    target.write_text(DENIED)
    detail = loop._no_handoff_detail(target, generic.ADAPTER, COMMAND)
    assert detail.startswith("; antigravity refused 2 tool call(s) (RunCommand")
    assert not detail.endswith(".")


def test_the_pause_detail_falls_back_without_a_refusal(tmp_path: Path):
    target = tmp_path / "out.log"
    target.write_text(CLEAN)
    detail = loop._no_handoff_detail(target, generic.ADAPTER, COMMAND)
    assert "refused" not in detail
    assert detail == generic.ADAPTER.diagnose(CLEAN)
