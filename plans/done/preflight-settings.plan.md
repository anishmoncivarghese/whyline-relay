<!-- whyline-plan v1 | source: hand | drafted-by: claude | created: 2026-11-15T12:30:00+05:30 -->
# Preflight never crashes: inline `--settings`, a safety net for every check, and a release

Runs now, by itself. It does not depend on any other plan, and agentdock's running `plans/quick-fixes.plan.md` relay does not depend on it until its last task (RDF-5, which raises whyline's minimum whyline-relay to the version PFS-3 publishes).

This file is the plan Relay runs. Each task is one `- [ ] ID: title` item. Detail lines stay indented. Do not add another checkbox line anywhere in this file. Do not implement from this plan until Relay starts the task.

Why:
- Since 0.2.42 (RPZ-8) the built-in Antigravity recipe passes its allow list as inline JSON: `--settings {"permissions": {"allow": [...]}}` (`src/whyline_relay/recipes.py`). The value is 741 characters long.
- `preflight._relay_setup` treats every `--settings` value as a file path and calls `is_file()` on it. A name over 255 characters makes the operating system raise `OSError: File name too long`, which nothing catches.
- So in any repository with Antigravity in a role or in the backup chain, `whyline-relay doctor`, `start` and `resume` end in a traceback. A repository whose own `config.toml` still has an older Antigravity command without `--settings` is not affected, which is why this went unseen.
- The same would happen for any other check that meets a value it did not expect: one uncaught error in one check takes down the whole preflight.

What the user gets:
- `doctor`, `start` and `resume` work with Antigravity in any role.
- A settings value that is neither inline JSON nor a usable file is reported as a normal failed check, in one line.
- If any preflight check breaks in future, the doctor says which check could not run and why, shows every other check, and never prints a traceback.
- whyline-relay 0.2.44 on PyPI.

Global constraints:
- No real vendor agents in tests; inject fake runners. No network.
- Keep every existing behaviour not named here. A missing settings file is still reported as `settings file is missing: <path>` with the hint `whyline-relay init`.
- No new dependency. Keep Python 3.11 compatibility.
- A settings value is never printed in full: at most its first 60 characters, then `…`.
- Edit only the files a task names, plus their tests. Run the focused tests and the complete suite (`uv run pytest -q`) after each task.

**Review focus.** Each item is pinned by the test named in its task. This list, plus each task's own tests, is the complete bar for approval. A reviewer who finds a gap outside it records `whyline handoff <task> --issue "follow-up: <gap>"` and approves, and never rejects for it.
- Every built-in recipe, in every role and in the backup chain, passes the relay setup check in a repository set up by `init`. PFS-1, `test_every_builtin_recipe_passes_setup`.
- Inline JSON after `--settings`, and in the `--settings=<value>` form, is not looked up as a file. PFS-1, `test_inline_json_settings_is_not_a_path`.
- A value the operating system refuses as a path is a failed check, not an exception. PFS-1, `test_unusable_settings_value_is_a_failed_check`.
- A check that raises is reported as a failed check and the checks after it still run. PFS-2, `test_a_broken_check_does_not_stop_the_rest`.

- [x] PFS-1: The setup check reads `--settings` values safely
  Edit `src/whyline_relay/recipes.py`, `src/whyline_relay/preflight.py`, `tests/test_preflight.py` and `tests/test_recipes.py` only.
  1. `recipes.settings_values(command) -> list[str]`: every value given to `--settings` in a command, in order, for both forms: `--settings <value>` and `--settings=<value>`. A trailing `--settings` with nothing after it gives the empty string.
  2. `recipes.settings_file(value) -> Path | None`: None when the value is inline JSON (after `lstrip()` it starts with `{`), otherwise `Path(value).expanduser()`. It never touches the disk.
  3. `preflight._relay_setup` uses both. For each value:
     - the empty string: `--settings has no path`, as today
     - inline JSON: when `json.loads` fails, `--settings is not valid JSON: <first 60 characters>…`; otherwise nothing is reported
     - a path: resolved against the repository as today; when `is_file()` raises `OSError`, `--settings is not a usable path: <first 60 characters>…`; when it returns False, `settings file is missing: <path>` as today
     All problems are joined with `; ` in one FAIL check with the hint `whyline-relay init`, as today.
  4. No recipe changes. Antigravity keeps its inline JSON.
  Tests: `test_every_builtin_recipe_passes_setup` (a repository set up the way `init` does it; each name in `recipes.RECIPES` placed in turn as implementer, tester, reviewer and in the backup chain, using the built-in command), `test_inline_json_settings_is_not_a_path` (both forms, with a 741-character value), `test_unusable_settings_value_is_a_failed_check` (a 300-character value that is not JSON; the message holds at most 60 characters of it), invalid inline JSON, the `--settings=` form with a missing file, and the existing missing-file test unchanged.

- [x] PFS-2: One broken check never takes down the preflight
  Edit `src/whyline_relay/preflight.py`, `src/whyline_relay/cli.py` and their tests only.
  1. In `preflight.run`, each check (the git check, `_whyline`, `_relay_setup`, `_programs`, `_logins`, `_role_checks`, the pipeline stage checks and every check after them) runs through one helper. When a check raises anything other than `KeyboardInterrupt` or `SystemExit`, the helper returns a FAIL check `<check name> could not run: <ErrorType>: <first line of the message, at most 200 characters>` with the hint `run whyline-relay doctor again; if it repeats, report it`, and the remaining checks run.
  2. When `_relay_setup` itself could not run, the checks that need its settings are skipped, as they are today when the config is missing.
  3. The two `ValueError`s `run` raises for a wrong `scope` or `allow_dirty` are kept: they are programming errors, not checks.
  4. `cmd_doctor`, `cmd_start`, `cmd_resume` and `_continue_plan` need no change of their own beyond what the tests require: they already print the checks and stop on a failure.
  Tests: `test_a_broken_check_does_not_stop_the_rest` (monkeypatch `_logins` to raise `OSError("boom")`; the result has the FAIL line and the checks that follow it), a raising `_relay_setup` skipping the settings-dependent checks, `KeyboardInterrupt` passing through, the 200-character cap, and `doctor` exiting with its error code and no traceback on stderr.

- [x] PFS-3: Publish whyline-relay with PFS-1 and PFS-2
  relay-profile: release
  Bump the version in `pyproject.toml`, `src/whyline_relay/__init__.py` and `uv.lock` to the next patch version (0.2.44). Write `docs/releases/v<version>.md` from every task since the previous release (`git log v<previous>..HEAD`), in user terms: say that `doctor`, `start` and `resume` crashed with `File name too long` when Antigravity held a role, in 0.2.42 and 0.2.43, and that upgrading fixes it in every repository with no config change. Run `uv run pytest -q` with the agent CLIs off PATH and `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`, and again on Python 3.11. Push, wait for green CI, then tag `v<version>`, push the tag and let the release workflow publish to PyPI.
  Do not upgrade the installed whyline tool while a relay is running in any repository on this Mac: the tool environment is shared, and agentdock's quick-fixes relay is using it. When no relay is running, run `uv --no-cache tool install --force whyline@latest`, confirm that `whyline-relay --version` shows the new version, and run `whyline relay doctor` in a repository whose tester is Antigravity.
