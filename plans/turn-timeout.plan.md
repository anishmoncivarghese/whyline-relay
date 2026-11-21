<!-- whyline-plan v1 | source: hand | drafted-by: claude | created: 2026-11-21T11:30:00+05:30 -->
# Turn timeout: hand a timed-out turn to the next backup, and keep every role's commands in the foreground

Runs now, by itself. Nothing depends on it. agentdock needs no change: its `whyline-relay>=0.2.45,<0.3` line already allows the version RTO-4 publishes.

This file is the plan Relay runs. Each task is one `- [ ] ID: title` item. Detail lines stay indented. Do not add another checkbox line anywhere in this file. Do not implement from this plan until Relay starts the task.

Why:
- On 2026-11-21 agentdock's agent-setup relay paused at LRV-1 round 2. Antigravity, as the tester, started the test suite in the background, then sat idle (`root agent idle; waiting for 1 background task(s) to finish`) until the 60-minute turn timeout killed it.
- A timeout always pauses: `run_agent` turns `agents.AgentTimeout` into `Paused` (`loop.py`, the `except agents.AgentTimeout` branch). The backup chain (grok) was never tried, though a capacity error on the same turn would have moved to it (`_failover_no_handoff`, the `overload` branch).
- Only the implementer's prompt says "never run anything in the background, and never end your turn while something you started is still running" (`prompts.py`, "How to finish"). The reviewer and tester prompts, which also run the test suite, don't.

What the user gets:
- A turn that runs out of time is given to the next agent in the backup chain, with the log line `antigravity ran out of time (60m); using grok for this turn`. The relay pauses only when no backup is left.
- For the rest of that task, the role skips the agent that timed out, so a hang isn't repeated every round. The next task goes back to the configured agent.
- Every role is told to run its commands in the foreground and wait for them.

Global constraints:
- **A timeout is not a limit.** Nothing is written to `limits.json` or `active-roles.json`. The fallback lasts for this task only and is held in memory, like the overload fallback.
- **A manual switch wins.** If you switched the role while the timed-out turn ran, the next turn follows your switch, as `roles switch` promises, and no fallback is queued.
- **Cancel and Ctrl+C are unchanged.** `AgentCancelled` and `KeyboardInterrupt` still pause or stop exactly as today.
- **The working tree is left as the timed-out agent left it.** The backup is told so in its prompt and carries on from there. Nothing is reset or stashed.
- No real vendor agents in tests; inject fake runners. No network. No test sleeps for the real timeout: use a timeout of seconds or patch `agents.run` to raise `AgentTimeout`.
- No new dependency. Keep Python 3.11 compatibility.
- Edit only the files a task names, plus their tests. Run the focused tests and the complete suite (`uv run pytest -q`) after each task.

**Review focus.** Each item is pinned by the test named in its task. This list, plus each task's own tests, is the complete bar for approval. A reviewer who finds a gap outside it records `whyline handoff <task> --issue "follow-up: <gap>"` and approves, and never rejects for it.
- A timed-out turn is run again by the next backup, in the same role, the same round. RTO-2, `test_timeout_hands_the_turn_to_the_backup`.
- With no backup left, or every backup also timed out, the relay pauses with one message naming each agent that ran out of time. RTO-2, `test_timeout_with_an_exhausted_chain_pauses`.
- The agent that timed out isn't used again by that role in the same task, and is used again by the next task. RTO-2, `test_timed_out_agent_is_skipped_for_the_rest_of_the_task`.
- A role switched by you during the timed-out turn follows your switch. RTO-2, `test_timeout_after_a_manual_switch_follows_the_switch`.
- Nothing about a timeout is written to `limits.json` or `active-roles.json`. RTO-2, `test_timeout_writes_no_limit_or_override`.

- [ ] RTO-1: Every role runs its commands in the foreground
  Edit `src/whyline_relay/prompts.py` and `tests/test_prompts.py` (or the test file that already covers the prompt templates) only.
  1. Move the implementer's sentence into one constant, `FOREGROUND = "Run every command to completion and read its result before moving on or finishing your turn: never run anything in the background, and never end your turn while something you started is still running."`, and use it in the implementer's "How to finish".
  2. Add the same constant to the reviewer prompt, right before "Run the project's tests yourself before approving", and to the tester prompt, right before "Run the project's own test command yourself".
  3. Add it to every pipeline stage prompt and the plan-draft and plan-review prompts that tell the agent to run a command. Leave prompts that run no commands as they are.
  4. Add `TIMED_OUT_NOTE = "The previous agent in this role ran out of time before handing off. Its unfinished changes, if any, are in the working tree; check them before you build on them."`. Nothing uses it yet; RTO-2 does.
  Tests: `test_every_command_running_prompt_says_foreground` (implementer, reviewer, tester and each stage prompt that mentions a test command contain `FOREGROUND` exactly once), the implementer prompt's text otherwise unchanged.

- [ ] RTO-2: A timed-out turn goes to the next backup
  Edit `src/whyline_relay/loop.py` and `tests/test_loop_failover.py`, `tests/test_loop_pipeline.py` only.
  1. Add `class TurnTimedOut(Exception)` with `agent: str`, `minutes: int`, `target: Path`. In `run_agent`, `except agents.AgentTimeout` raises `TurnTimedOut` instead of `Paused`. `AgentCancelled`, `AgentMissing` and `KeyboardInterrupt` are untouched.
  2. Both places that call `_failover_no_handoff` (the single-role loop and the pipeline loop) catch `TurnTimedOut` around `run_agent` and call `_failover_timeout(root, settings, *, agent, role, minutes, target, echo, ephemeral, ephemeral_tried, ephemeral_basis, turn_override, timed_out, live) -> None`. It returns to retry the turn, or raises `Paused`.
  3. `_failover_timeout` follows the `overload` branch of `_failover_no_handoff`: the manual-switch check first (log `antigravity ran out of time; test follows your switch from its next turn`, queue nothing), then `failover.next_backup(failover.live_chain(root, settings), tried, failover.limited_agents(root))`, where `tried` is `ephemeral_tried[role]` plus `timed_out[role]` plus `agent`. A backup found: set `ephemeral[role]`, `ephemeral_tried[role]`, `ephemeral_basis`, add `agent` to `timed_out[role]` and log `antigravity ran out of time (60m); using grok for this turn`.
  4. No backup: raise `Paused("antigravity ran out of time (60m) on test, and no backup is left (grok also ran out of time); resume to try again, or change the timeout or the test agent", target)`. The bracket lists the other agents in `timed_out[role]`, and is left out when there are none.
  5. `timed_out: dict[str, set[str]]` is created with the other per-task dicts in `run_task` (and the pipeline's equivalent), so it is empty for each new task. When choosing a role's agent for a turn, an agent in `timed_out[role]` is replaced by the next backup not in it, logged once per task as `test: skipping antigravity for the rest of LRV-1 (it ran out of time)`. If none is left, the configured agent is used.
  6. The retried turn's prompt gets `prompts.TIMED_OUT_NOTE` through the existing `prompt_suffix`.
  7. Nothing is written to `limits.json` or `active-roles.json`.
  Tests: `test_timeout_hands_the_turn_to_the_backup` (fake codex raises `AgentTimeout`, aider takes the turn, the task commits, the log line), `test_timeout_with_an_exhausted_chain_pauses` (both raise; the exact message), `test_timed_out_agent_is_skipped_for_the_rest_of_the_task` (round 2 of the same role uses the backup; the next task uses codex again), `test_timeout_after_a_manual_switch_follows_the_switch`, `test_timeout_writes_no_limit_or_override`, a pipeline stage timing out moving to the backup, and the retried prompt containing `TIMED_OUT_NOTE`.

- [ ] RTO-3: Status and docs
  Edit `src/whyline_relay/status.py`, `README.md` (or the docs page that covers backups and timeouts) and their tests only.
  1. While a role is skipping a timed-out agent, `whyline-relay status` shows `test: grok (antigravity ran out of time)` on that role's line, read from the same in-run record `status` already uses for an overload stand-in. If no such record exists, write the role's stand-in and reason to `running.json` in RTO-2's `_failover_timeout` and read it here.
  2. The docs say: a timed-out turn goes to the next backup and the relay pauses only when none is left; the agent is skipped for the rest of that task; an empty backup chain keeps the old behaviour (pause on the first timeout); every role is told to keep commands in the foreground.
  Tests: the status line for a timed-out stand-in, and no line when nothing timed out.

- [ ] RTO-4: Publish whyline-relay with RTO-1..3
  relay-profile: release
  Bump the version in `pyproject.toml`, `src/whyline_relay/__init__.py` and `uv.lock` to the next patch version (0.2.46). Write `docs/releases/v<version>.md` from every task since the previous release (`git log v<previous>..HEAD`), in user terms: a turn that runs out of time moves to the next backup instead of pausing, the agent is skipped for the rest of that task, the relay pauses only when no backup is left, and every role now keeps its commands in the foreground. Run `uv run pytest -q` with the agent CLIs off PATH and `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`, and again on Python 3.11. Push, wait for green CI, then tag `v<version>`, push the tag and let the release workflow publish to PyPI.
  Do not upgrade the installed whyline tool while a relay is running in any repository on this Mac: the tool environment is shared.
