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

- [ ] RPZ-1: The pause request and `whyline-relay pause`
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

- [ ] RPZ-2: Pause at safe points
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

- [ ] RPZ-3: A cut-off turn is recorded and handed on
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

- [ ] RPZ-4: Pause state for status and the console
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

- [ ] RPZ-5: Publish whyline-relay with RPZ-1..4 and RPZ-6
  relay-profile: release
  Bump the version in `pyproject.toml`, `src/whyline_relay/__init__.py` and `uv.lock` to the next patch version. Write `docs/releases/v<version>.md` from every task since the previous release, in user terms. Document `pause` in the README. Run `uv run pytest -q` with the agent CLIs off PATH and `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`, and again on Python 3.11. Push, wait for green CI, then tag `v<version>`, push the tag and let the release workflow publish to PyPI. Afterwards run `uv --no-cache tool upgrade whyline --upgrade-package whyline-relay` and confirm that `whyline-relay --version` shows the new version. Then, in agentdock, mark PZ-1 done when that plan reaches it.
