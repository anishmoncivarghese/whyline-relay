import json

from whyline_relay import init
from whyline_relay.adapters import claude, codex


def test_claude_permission_files_match_the_existing_allowlist():
    assert claude.ADAPTER.permission_files("python") == {
        "claude-settings.json": json.dumps(init.allowlist("python"), indent=2) + "\n"
    }


def test_claude_diagnose_names_denied_commands_and_settings_file():
    denied = json.dumps(
        {
            "type": "result",
            "permission_denials": [
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "git commit -m x"},
                }
            ],
        }
    )
    detail = claude.ADAPTER.diagnose(denied)
    assert "git commit -m x" in detail
    assert "claude-settings.json" in detail


def test_codex_diagnose_uses_the_last_non_blank_line():
    assert codex.ADAPTER.diagnose("a\nb\n") == '; its last output was: "b"'


def test_codex_and_claude_declare_the_real_model_flag():
    assert codex.ADAPTER.model_flag == ("--model",)
    assert claude.ADAPTER.model_flag == ("--model",)


def test_generic_has_no_model_flag():
    from whyline_relay.adapters import generic

    assert generic.ADAPTER.model_flag is None
