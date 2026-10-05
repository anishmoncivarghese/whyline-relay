from whyline_relay.adapters import generic, grok

CANCELLED = '{\n  "text": "x",\n  "stopReason": "cancelled",\n  "sessionId": "s-1"\n}\n'
FINISHED = '{\n  "text": "x",\n  "stopReason": "end_turn",\n  "sessionId": "s-1"\n}\n'
COMMAND = ["grok", "--output-format", "json", "--allow", "Edit",
           "--allow", "Bash(git add:*)", "-p"]


def test_a_cancelled_grok_turn_names_its_session_for_resuming():
    assert grok.resumable_session(generic.ADAPTER, CANCELLED, COMMAND) == "s-1"


def test_a_finished_or_non_grok_turn_is_not_resumed():
    assert grok.resumable_session(generic.ADAPTER, FINISHED, COMMAND) is None
    assert grok.resumable_session(generic.ADAPTER, CANCELLED, ["agy", "-p"]) is None
    no_session = CANCELLED.replace('"sessionId": "s-1"', '"other": 1')
    assert grok.resumable_session(generic.ADAPTER, no_session, COMMAND) is None


def test_resume_command_keeps_the_prompt_flag_last():
    assert grok.resume_command(COMMAND, "s-1") == [
        *COMMAND[:-1], "--resume", "s-1", "-p",
    ]


def test_a_command_not_ending_in_the_prompt_flag_cannot_be_resumed():
    assert grok.resume_command(["grok", "--prompt-file"], "s-1") is None
    assert grok.resumable_session(
        generic.ADAPTER, CANCELLED, ["grok", "--prompt-file"]
    ) is None


def test_resume_prompt_lists_the_allowed_rules():
    prompt = grok.resume_prompt(COMMAND)
    assert "was stopped" in prompt
    assert "Edit" in prompt and "Bash(git add:*)" in prompt
    assert "hand off" in prompt
