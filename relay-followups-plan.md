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

## Found while running agentdock's personal-assistant plan (2026-10-27..28)

Claude, as implementer, paused ASST-17 twice as "blocked". It ran `python -m pytest`, which its allow list refuses and which cannot work there anyway: there is no `python`, and the system `python3` has no pytest. Grok gets `GROK_RULES` through `--rules`; Claude's command gets no equivalent, so it never learns the allowed test command. Its refusal text, "This command requires approval", is not one of `SANDBOX_MARKERS`, so the relay paused with a long "reported blocked" reason instead of naming the command. agentdock works around this today with a hand-written `--append-system-prompt` in its own `[agents.claude]`. The console side is agentdock's `plans/relay-recovery-lessons.plan.md` (LSN-1..6).

- [ ] RLY-4: A `test_command` setting with one saver
  Edit `src/whyline_relay/config.py`, `src/whyline_relay/init.py` and `src/whyline_relay/cli.py`, with tests in `tests/`.
  Add an optional top-level `test_command = "..."` to config.toml. `config.test_command(root) -> str` returns it when set. Otherwise it detects one: `uv run pytest -q` for a python stack with `uv.lock`, `pytest -q` for any other python stack, `npm test` for node, and "" for base (reuse `init.detect_stack`).
  Add `config.save_test_command(root, command)`. It writes or removes the key and leaves every other line of the file unchanged. An empty string removes it.
  Add `whyline relay test-command [COMMAND]`. With no argument it prints the current value and says whether it was set or detected. With an argument it calls `save_test_command`. agentdock's console calls the same function (LSN-5).
  Tests: each detection case, a set value wins, saving keeps comments and other keys, an empty string removes the key, and the CLI prints and saves through `save_test_command`.

- [ ] RLY-5: Claude is told its limits and the test command
  Edit `src/whyline_relay/adapters/claude.py` and `src/whyline_relay/config.py` (where builtin agents get `model`, about line 313), with tests in `tests/`.
  Add `CLAUDE_RULES` next to `recipes.GROK_RULES`, with the same headless rules plus one line: "Run tests only with `<test_command>`." When the test command is "", leave that line out.
  When the claude command is built, append `--append-system-prompt <rules>`. Skip this when the configured command already contains `--append-system-prompt` or `--append-system-prompt-file`, so a repository's own text wins. The prompt stays the last argument.
  Tests: the flag is added with the detected and the configured test command, a configured flag is not doubled, the prompt is still last, and codex and generic agents are unchanged.

- [ ] RLY-6: A refused command is named in the pause reason
  Edit `src/whyline_relay/loop.py` and `src/whyline_relay/adapters/claude.py`, with tests in `tests/`.
  Add "requires approval" to `SANDBOX_MARKERS`. When Claude's output has `permission_denials`, the pause reason is `claude was denied: <first command, up to 80 chars>. Allowed test command: <test_command>.`, using `_sandbox_pause_reason` instead of `_blocked_reason`. Other agents and other pauses are unchanged.
  Tests: Claude output with a denial gives the new reason, "requires approval" in a summary counts as a refusal, and a blocked handoff with no denial keeps today's reason.

- [ ] RLY-7: An implementer stopped only by a refused test command moves on to test
  Edit `src/whyline_relay/loop.py` and `src/whyline_relay/prompts.py`, with tests in `tests/`.
  When the draft stage's handoff is `blocked`, it asks no question, every refused command is a test command (it contains `pytest`, `npm test`, or the configured `test_command`'s first word), and the working tree differs from the task base, follow the stage's `ready` route instead of pausing. Print "<agent> could not run `<command>`; the tester will run the tests." The next stage's prompt gets that line in its review feedback.
  The test and review stages keep today's rule: a refused test command is never a pass.
  In the implement prompt, change "Use blocked for a denied test command" to tell the agent to hand off `ready` with the denied command in its summary when the code is written and only the test run was refused.
  Tests: a test-only refusal with changes moves to test, the same refusal with no changes pauses, a refusal of a non-test command pauses, a handoff with a question pauses, and a tester refusal still pauses.
