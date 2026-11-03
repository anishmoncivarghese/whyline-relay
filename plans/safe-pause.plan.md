<!-- whyline-plan v1 | source: hand | drafted-by: claude | created: 2026-11-02T15:00:00+05:30 -->
# Safe pause: pause a running relay between turns, so nothing is lost

Runs after `plans/documents.plan.md` (RDOC-1..3, released as 0.2.41). Needed by agentdock's `plans/safe-pause.plan.md` (PZ-1 waits for this release). Design: agentdock `docs/superpowers/specs/2026-11-02-safe-pause-design.md`. Read it before any task.

This file is the plan Relay runs. Each task is one `- [ ] ID: title` item. Detail lines stay indented. Do not add another checkbox line anywhere in this file. Do not implement from this plan until Relay starts the task.

Why:
- Today `whyline-relay stop`, Ctrl+C and the STOP file kill the working agent mid-turn. Its edits stay on disk, its handoff is lost, and Resume redoes the turn without telling the next agent about the half-done edits.
- The user wants a Pause that waits for a safe point: the current turn finishes and hands off, then the relay pauses before the next agent starts. Resume continues with the next turn.

Global constraints:
- No real vendor agents in tests; inject fake runners.
- Keep every existing behaviour not named here. STOP keeps killing the agent within about 0.1 s; the PAUSE file is never watched mid-turn.
- No new dependency.
- Run the focused tests and the complete suite after each task.

- [x] RPZ-1: The pause request and `whyline-relay pause`
  Edit `src/whyline_relay/loop.py`, `src/whyline_relay/cli.py` and their tests only.
  1. In `loop.py`, add:
     - `pause_path(root)` → `.whyline/relay/PAUSE`
     - `request_pause(root, *, now=False, clock=None)`, which writes JSON `{"requested_at": iso, "now": bool}` atomically
     - `pause_request(root) -> PauseRequest | None`
     - `cancel_pause(root)`
  2. In the CLI:
     - `whyline-relay pause` requests a safe pause. It prints "Pause requested: the relay pauses after the current agent finishes its turn." and exits 0. With no relay running, it prints "No relay is running here." and exits 1.
     - `whyline-relay pause --cancel` withdraws the request.
     - `whyline-relay pause --now` writes the request with `now: true`, then writes the STOP file, as `stop` does.
  Tests:
  - the file's contents
  - cancel
  - `--now` writes both files
  - the STOP watcher ignores PAUSE

- [x] RPZ-2: Pause at safe points
  Edit `src/whyline_relay/loop.py`, `src/whyline_relay/state.py` and their tests only.
  1. Add `PAUSED_BY_USER = "paused by you"`. `RelayState` gains `paused_by: str = ""` and `paused_at: str = ""`. Old state files still load.
  2. Check `pause_request(root)` at these safe points. When there is a request, delete the file and raise `Paused(PAUSED_BY_USER)` with `paused_by="user"`:
     - in the configured-pipeline stage loop, after the previous stage's record is saved and the next stage is chosen, before the next agent runs
     - at the same point between implementer and reviewer turns in the legacy loop
     - at the top of `run_plan`'s task loop, after the tick commit
  3. The saved stage, `stage_visits` and round must make `resume` run the *next* turn. A finished turn is never rerun, and the visit cap and round cap aren't charged twice.
  4. Any other pause, a finished plan, and `resume` all delete a leftover PAUSE file.
  Tests, with a fake agent that writes PAUSE during its turn:
  - the turn finishes and hands off
  - the relay exits with the pause code and the reason "paused by you"
  - `resume` runs the next stage, and the fake records no second run of the finished stage
  - the same through the legacy loop and between tasks
  - a limit pause clears the request

- [x] RPZ-3: A cut-off turn is recorded and handed on
  Edit `src/whyline_relay/loop.py`, `src/whyline_relay/state.py`, `src/whyline_relay/prompts.py` and their tests only.
  1. `RelayState` gains `cut_off: dict | None = None`, holding `stage`, `role`, `agent` and `changed_files`.
  2. In the KeyboardInterrupt/STOP pause path:
     - Record `cut_off`, with changed files from `git status --porcelain --untracked-files=all` minus `.whyline/relay/**`.
     - If a PAUSE file with `now: true` exists, the reason is "paused now by you; <agent>'s turn was cut off". Otherwise it is today's text.
     - Delete PAUSE and STOP.
  3. On `resume` with `cut_off` set, the next agent's prompt starts with: "The previous turn (<role> <agent>) was cut off before it handed off. It left changes in: <files>. Continue from them; don't start over and don't discard them." Clear `cut_off` once that turn starts.
  Tests:
  - Ctrl+C mid-turn records the files
  - `pause --now` gives the "paused now" reason
  - the resume prompt contains the files
  - `cut_off` is cleared after the turn

- [x] RPZ-4: Pause state for status and the console
  Edit `src/whyline_relay/status.py` (create it if status lives elsewhere, and keep the existing status function), `src/whyline_relay/cli.py` and their tests only.
  1. Add `pause_state(root, *, clock=None) -> PauseState(kind, since, agent, task_id, round, stage, reason)`. `kind` is:
     - `none`
     - `pausing`, when a relay is running and PAUSE exists
     - `paused_by_user`
     - `cut_off`, when paused with `cut_off` set
     - `paused`, for any other saved pause
  2. `whyline-relay status` prints one line for it:
     - "Pausing: waiting for codex to finish its turn (1m 20s)"
     - "Paused by you at RPZ-2, round 2. Run `whyline-relay resume` to continue."
  Tests: each kind.

- [ ] RPZ-6: Retry stage gives every capped stage one more visit
  Edit `src/whyline_relay/cli.py`, `src/whyline_relay/loop.py`, `src/whyline_relay/state.py` and their tests only.
  Why: on 2026-11-02, agentdock's FIX-2 had used 6 of 6 visits in draft, test and review. **Retry stage** gave one visit back to `test` only, so the run passed its tests, moved to `review` and paused at once on review's cap. The user had to press Retry stage a second time before the reviewer ran.
  1. `_retry_capped_stage` gives one visit back to every stage whose saved count is at or above its cap, not only the stage named in the pause reason. It prints "Retrying stages 'test' and 'review' once more." (or the one stage when only one is capped), and the `whyline note` names them all.
  2. The pipeline fingerprint and `max_visits` stay unchanged, as today.
  3. It still refuses when the run isn't paused on a stage visit cap.
  Tests:
  - with test and review both at the cap, one retry lets a fake run pass through test and review and reach "@complete" without a second pause
  - with only one stage capped, only that one changes
  - the printed line and note name every stage given back
  - a non-cap pause is still refused

- [ ] RPZ-7: A rejection that mentions "sandbox" is not a refused command, and a misread pause can be sent back
  Edit `src/whyline_relay/loop.py`, `src/whyline_relay/cli.py` and their tests only.
  Why: on 2026-11-02, agentdock's FIX-2 reviewer rejected with "Strip Codex attached short config overrides such as -c=sandbox_workspace_write.network_access=true and add a regression test". `_summary_reports_sandbox_refusal` matched the bare word "sandbox" in `SANDBOX_MARKERS`, so the relay paused with "codex could not run a required command" instead of sending the task back to Claude. Resume rechecks the same summary (the `resumed.kind == "advance"` path), so every Resume paused again: a deadlock no console button could clear.
  1. Drop the bare `"sandbox"` and `"not permitted"` from `SANDBOX_MARKERS`. Match refusal phrases only: "permission denied", "operation not permitted", "was denied", "requires approval", "blocked by the sandbox", "sandbox denied", "sandbox refused".
  2. Treat a summary as a refusal only when the handoff isn't a normal rejection carrying issues. A `rejected` handoff with at least one `--issue` line, or a summary that asks for a code change ("add a regression test", "fix", "strip"), is routed as a normal send-back.
  3. Add `whyline-relay resume --as-rejection`. On a paused refused-command pause, it routes the saved handoff as a normal rejection to the earlier stage and records a `whyline note` saying so. It refuses on any other pause.
  Tests:
  - the exact FIX-2 summary above routes back to draft and doesn't pause
  - "codex: permission denied running uv" still pauses
  - `--as-rejection` clears a refused-command pause and runs the earlier stage
  - `--as-rejection` refuses on a limit pause

- [ ] RPZ-8: Antigravity runs with a per-run allow list, and a refused command is reported
  Edit `src/whyline_relay/recipes.py`, `src/whyline_relay/loop.py` (only `_no_handoff_detail`), `src/whyline_relay/preflight.py` and `README.md`, create `src/whyline_relay/adapters/antigravity.py`, and add their tests only.
  Why: google-antigravity/antigravity-cli#548 is fixed. Checked on 2026-11-03 with agy 1.4.12 on macOS:
  - `agy --settings '<json>'` applies settings to that run only. Its `permissions.allow` replaces the global file's list: an empty list refused a command even though the global file allows `command(*)`.
  - Rules match by command prefix: `command(git status)` ran `git status --short`. `git push` was refused, and so was `git status && touch x`. A `touch` rule did not let `rm` through.
  - A refused call ends the turn in seconds. It still exits 0 with `"status": "SUCCESS"`, and lists `{"action": "command", "display_name": "RunCommand"}` under `denied_actions`, without the command itself.
  - A run with only `--settings` read a file in a folder missing from the global `trustedWorkspaces`.
  1. In `recipes.py`, add `ANTIGRAVITY_ALLOW`:
     - `read_file(*)`, `write_file(*)` and `edit_file(*)`
     - `command(<prefix>)` for exactly the commands grok's recipe allows: git add, git commit, git diff, git status, git log, git show, git rev-parse, whyline, python3, uv run, uv, mkdir, ls, find, touch, cat, grep, head, tail, wc, diff, pwd, pytest, .venv/bin/pytest and .venv/bin/python
     - no `command(*)`, no git push, no rm
  2. The antigravity recipe gains `"--settings", json.dumps({"permissions": {"allow": list(ANTIGRAVITY_ALLOW)}})` just before `-p`. `-p` stays last, and a repository's own `[agents.antigravity]` still wins.
  3. In `adapters/antigravity.py`, add:
     - `payload(adapter, text, command)`, which returns the JSON object only for a generic agent whose `command[0]` is named `agy`
     - `denied_detail(adapter, text, command) -> str`, which returns, when `denied_actions` is non-empty: "antigravity refused N tool call(s) (RunCommand, ...). agy names only the tool type, not the command; add what it needs to the --settings allow list in [agents.antigravity], or run it yourself." Otherwise it returns "".
  4. `_no_handoff_detail` tries `denied_detail` after grok's `cancellation_detail`. It is shown to the human only, never used to route.
  5. The preflight warning becomes: "antigravity is a generic agent and needs agy 1.4.12 or later (github.com/google-antigravity/antigravity-cli#548). The relay gives it a per-run allow list; a refused tool call is reported but agy does not name the command, and the relay does not manage its login -- see README, 'Using Antigravity today'".
  6. The README's recipe and allow-list paragraphs describe the per-run list and these results.
  Not in this task:
  - `antigravity.trust` and the console's Trust prompt stay. Dropping the global `trustedWorkspaces` edit is a later agentdock change, after this ships.
  - agentdock's copy of the warning text, in `relay_ops._ANTIGRAVITY_GENERIC`
  Tests:
  - the recipe has `--settings` right before `-p`, and its JSON parses to the exact list in item 1
  - the list holds no `command(*)`, `git push` or `rm`
  - a repository's `[agents.antigravity]` command is used unchanged
  - an agy JSON result with `denied_actions` gives the detail, one without gives ""
  - a non-agy generic agent gives ""
  - the preflight warning mentions 548 and the per-run allow list
  Review focus (the complete list; it is the full bar for approval):
  1. `-p` is the last argument and the `--settings` value is valid JSON
  2. the allow list is exactly item 1's: no wildcard command, no push, no rm
  3. a repository's own `[agents.antigravity]` wins
  4. `denied_detail` reads only agy's JSON and never routes
  5. the full suite passes
  A gap outside this list is not a rejection. Record it with `whyline handoff RPZ-8 --issue "follow-up: <gap>"` and approve.

- [ ] RPZ-5: Publish whyline-relay with RPZ-1..4 and RPZ-6..8
  relay-profile: release
  Bump the version in `pyproject.toml`, `src/whyline_relay/__init__.py` and `uv.lock` to the next patch version. Write `docs/releases/v<version>.md` from every task since the previous release, in user terms. Document `pause` in the README. Run `uv run pytest -q` with the agent CLIs off PATH and `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`, and again on Python 3.11. Push, wait for green CI, then tag `v<version>`, push the tag and let the release workflow publish to PyPI. Afterwards run `uv --no-cache tool upgrade whyline --upgrade-package whyline-relay` and confirm that `whyline-relay --version` shows the new version. Then, in agentdock, mark PZ-1 done when that plan reaches it.
