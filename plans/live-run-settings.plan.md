<!-- whyline-plan v1 | source: hand | drafted-by: claude | created: 2026-11-15T22:30:00+05:30 -->
# Live run settings: change the timeout, the round cap and the backup order while the relay runs

Runs now, by itself. agentdock's `plans/agent-setup.plan.md` needs the version LRS-4 publishes for its last part (LRV-1..3), and nothing else depends on it.

This file is the plan Relay runs. Each task is one `- [ ] ID: title` item. Detail lines stay indented. Do not add another checkbox line anywhere in this file. Do not implement from this plan until Relay starts the task.

Why:
- While a relay runs, the user can already change which agent holds a role (`roles switch`, RLY-13) and an agent's model (`roles model`, RLY-18). Both work because the loop re-reads a small file before every turn.
- The turn timeout, the round cap and the backup order can't be changed the same way. `loop` reads `settings.timeout_minutes`, `settings.max_rounds` and `settings.backup_chain` from the `Config` loaded once at `start`, so a change to `config.toml` does nothing until the relay is stopped and started again.
- A task that needs a longer turn or one more round today means stopping the relay.

What the user gets:
- `whyline-relay settings timeout 45`, `settings rounds 5` and `settings backup codex grok` change the running relay from its next turn, with no stop.
- `--keep` also saves the value to `config.toml` for later runs. Without it the change lasts for this run only.
- `status` shows a changed value as `timeout 45m (was 30m, changed by you)`.
- An agent that is only a backup can have its model switched with `roles model`, which already works and is now pinned by a test.

Global constraints:
- **The running turn is never touched.** No signal, no change to its timeout. A change applies from the next turn, the next round check, or the next time a backup is needed.
- **The running process is told nothing.** The loop re-reads `.whyline/relay/run-settings.json` where it reads the values, as it does for `model-overrides.json`.
- **A file that can't be read changes nothing.** The loop uses the configured value and logs one line.
- No real vendor agents in tests; inject fake runners. No network.
- No new dependency. Keep Python 3.11 compatibility.
- Edit only the files a task names, plus their tests. Run the focused tests and the complete suite (`uv run pytest -q`) after each task.

**Review focus.** Each item is pinned by the test named in its task. This list, plus each task's own tests, is the complete bar for approval. A reviewer who finds a gap outside it records `whyline handoff <task> --issue "follow-up: <gap>"` and approves, and never rejects for it.
- A turn that is already running keeps the timeout it started with. LRS-2, `test_running_turn_keeps_its_timeout`.
- A round cap below the round a task is already on is refused, never applied. LRS-1, `test_round_cap_below_current_round_is_refused`.
- A backup order may only name agents that are set up, each once, and never changes who is running now. LRS-1, `test_backup_order_is_validated`.
- An unreadable `run-settings.json` leaves the configured values in force. LRS-2, `test_unreadable_file_changes_nothing`.
- Without `--keep`, `config.toml` is byte for byte what it was, and the file is gone when the run ends. LRS-1, `test_without_keep_nothing_lasts`.

- [x] LRS-1: The run-settings file and its rules
  Create `src/whyline_relay/run_settings.py` and `tests/test_run_settings.py`. Edit `src/whyline_relay/setup.py` (or the module that holds the relay's run-time `.gitignore` lines, RLY-17) and its tests.
  1. `RunSettings`: `timeout_minutes: int | None`, `max_rounds: int | None`, `backup_chain: tuple[str, ...] | None`, `since`, `keep`. None means "not changed".
  2. `path(root)` is `.whyline/relay/run-settings.json`. `read(root) -> RunSettings` raises `Unreadable` on bad JSON or a wrong shape. `effective(root, settings) -> Effective` (`timeout_minutes`, `max_rounds`, `backup_chain`, and `changed: dict[str, tuple[old, new]]`) never raises: an unreadable file gives the configured values and `problem` set to one line.
  3. `set_timeout(root, settings, minutes, *, keep=False) -> str`: 0 means no limit, otherwise 1 to 1440. Returns `Timeout 45m from the next turn.` A value equal to the configured one removes the change.
  4. `set_rounds(root, settings, rounds, *, keep=False, current_round=None) -> str`: 1 to 99. When `current_round` is given and `rounds < current_round`, it raises `RunSettingsError("<task> is already on round 4; the cap can't go below that")`. Returns `Round cap 5 from the next check.`
  5. `set_backup(root, settings, chain, *, keep=False) -> str`: every name must be in `settings.agents`, each once, and none may be the configured agent of all three roles. An empty list means no backups. Returns `Backups: codex, then grok, from the next time one is needed.`
  6. `reset(root, what=None) -> str` removes one change or all of them. `clear(root, *, completed)` removes the file when a run ends; changes made with `keep` are already in `config.toml`.
  7. With `keep`, the value is written to `config.toml` with the same careful text edit `roles._upsert_agent_model` uses: only that key's line changes.
  8. `run-settings.json` joins the run-time files the relay's `.gitignore` covers and its commit step leaves out.
  Tests: `test_round_cap_below_current_round_is_refused`, `test_backup_order_is_validated`, `test_without_keep_nothing_lasts`, each setter's message and its bounds, a value equal to the configured one removing the change, `keep` changing only its own line, `reset` for one and for all, and the `.gitignore` line.

- [x] LRS-2: The loop uses the changed values
  Edit `src/whyline_relay/loop.py`, `src/whyline_relay/failover.py` and their tests only.
  1. Where a turn is started, its timeout comes from `run_settings.effective(root, settings).timeout_minutes`, read once for that turn.
  2. Every place that compares a round with `settings.max_rounds`, and every message that prints it, uses the effective cap, read at that check.
  3. Every place that walks `settings.backup_chain` uses the effective chain, read at that moment. An agent that is standing in for a role keeps its turn when it is removed from the chain; it is just not chosen again.
  4. When a value differs from the one used for the previous turn, the log gets one line: `timeout now 45m (changed by you)`.
  5. An unreadable file logs `run-settings.json can't be read; using the configured values` once per change of the file, not once per turn.
  6. `clear(root, completed=…)` is called where `roles.clear_model_overrides` is called.
  7. A relay paused on the round cap resumes when the cap is raised above the task's round and `resume` is run; nothing resumes by itself.
  Tests: `test_running_turn_keeps_its_timeout` (a change during a fake turn applies to the next one only), `test_unreadable_file_changes_nothing`, a raised cap letting a task take a fourth round, a changed chain used at the next failover, a removed stand-in finishing its turn, the single log line, and the file cleared at the end of the run.

- [x] LRS-3: `whyline-relay settings`, and `status`
  Edit `src/whyline_relay/cli.py`, `src/whyline_relay/status.py`, `src/whyline_relay/roles.py` (tests only, unless the backup test below fails) and their tests.
  1. `whyline-relay settings` prints the three values, each as `timeout 45m (was 30m, changed by you)` or `timeout 30m`.
  2. `whyline-relay settings timeout <minutes> [--keep]`, `settings rounds <n> [--keep]`, `settings backup [<agent> …] [--keep]` and `settings reset [timeout|rounds|backup]` call the functions in LRS-1 and print their message. `rounds` passes the running task's round from `running.json` as `current_round`. Errors exit 2 with the message.
  3. They work whether or not a relay is running. With none running, `--keep` is implied and the message says `Saved for the next run.`
  4. `status` adds a `Settings:` line with any changed value, and `running.json` records the timeout and round cap the running turn started with.
  5. `roles model <agent> <model>` for an agent that is in the backup chain and in no role is accepted and applies when that agent next runs. If it is refused today, fix `roles.switch_model` so it is not.
  6. Tools such as the whyline console call `run_settings.set_timeout`, `set_rounds`, `set_backup`, `reset` and `effective` directly.
  Tests: each command and its message, the exit code on a bad value, the no-relay case, the `status` line, `running.json`'s two fields, and `test_backup_only_agent_can_switch_model`.

- [ ] LRS-4: Publish whyline-relay with LRS-1..3
  relay-profile: release
  Bump the version in `pyproject.toml`, `src/whyline_relay/__init__.py` and `uv.lock` to the next patch version (0.2.45). Write `docs/releases/v<version>.md` from every task since the previous release (`git log v<previous>..HEAD`), in user terms: the three `settings` commands, that a change applies from the next turn and never to the running one, what `--keep` does, and that a backup agent's model can be switched with `roles model`. Run `uv run pytest -q` with the agent CLIs off PATH and `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`, and again on Python 3.11. Push, wait for green CI, then tag `v<version>`, push the tag and let the release workflow publish to PyPI.
  Do not upgrade the installed whyline tool while a relay is running in any repository on this Mac: the tool environment is shared.
