# whyline-relay follow-ups found while running the Idea Inbox plan (2026-10-21..22)

Found in agentdock while the relay ran `plans/idea-inbox.plan.md`. The
console-side fixes are agentdock tasks AGS-1..5 and RSV-1..4. These three
belong in whyline-relay itself.

Global constraints:

- No real vendor agents in tests; inject fake runners.
- Keep every existing behaviour not named here.
- Run the focused tests and the complete suite after each task.

- [ ] RLY-1: Accept commits that only add tasks after the current one
  Edit `src/whyline_relay/loop.py` (`_settings_commits`, `_advance_base_past_settings`), with tests in `tests/`. Today a running or paused task accepts only config.toml-only commits in `base..HEAD`. A plan commit that adds tasks for later (agentdock added AGS-1..5 and AWK-1 this way) makes the task pause at approval with "HEAD moved", so every insertion had to wait for a pause and a fresh `start`.
  Also accept a commit that touches only the plan file (and `.whyline/decisions.md`, where the planning note goes), when parsing the plan before and after shows that the current task's text and checkbox are unchanged, every earlier task is unchanged, and the only difference is unchecked tasks added or edited after the current one. Print it like a settings commit: "Plan changed during <task> (<sha> <subject>); continuing from <head>." Anything else keeps today's pause.
  Tests: adding a task after the current one is accepted and the base advances; editing the current task's text is refused; ticking or unticking any task is refused; a plan-plus-code commit is refused; a decisions.md-only commit alongside it is accepted.

- [ ] RLY-2: `roles status` names the agent that actually hit the limit
  Edit `src/whyline_relay/roles.py` (the status line, about line 35). It prints `{configured} {verb}` ("antigravity hit a usage or rate limit") when the agent that failed was `override.backup_for` (grok on 2026-10-21, after the user had changed the configured implementer).
  Use `override.backup_for` in that clause. When it differs from the configured agent, add "(configured is now <configured>)".
  Tests: an override whose backup_for differs from the configured agent names backup_for, with the note.

- [ ] RLY-3: Let generic agents take a model
  Edit `src/whyline_relay/config.py` and `src/whyline_relay/adapters/generic.py`, with tests in `tests/`. The generic adapter has `model_flag=None`, so `[agents.grok] model = "..."` is a ConfigError, though grok and agy both accept `--model`. agentdock's console currently writes `--model` into `command` instead (AGS-4).
  Allow `[agents.<name>] model_flag = ["--model"]` for generic agents. When it is set, `model` works as it does for claude and codex. Without it, keep today's error, and add to its message that `model_flag` can be set.
  Tests: grok with model_flag and model gets both appended; without model_flag the error mentions it; claude and codex are unchanged.
