# Grok prefixed cancellation JSON patch release

## Goal

Release `whyline-relay` 0.2.39 so a headless Grok permission denial is
recognized as a cancelled turn even when the CLI prints a diagnostic line
before its pretty-printed JSON result. The existing same-session resume path
must then run instead of pausing with `last output was: "}"`.

## Scope

- Add a regression test using the measured `permission required:` plus
  multi-line JSON log shape.
- Teach the shared JSON reader to parse a trailing complete JSON object after
  preceding diagnostic text.
- Preserve behavior for complete JSON documents, compact final-line JSON, and
  non-JSON output.
- Add release notes, bump both public version declarations to 0.2.39, build,
  verify, tag, push, and let the trusted-publishing workflow publish PyPI and
  the GitHub Release.
- Install the published package into Agentdock's `.venv` and verify that the
  imported version and parser behavior are 0.2.39.

## Verification

1. `uv run pytest -q tests/test_grok_resume.py`
2. `uv run pytest -q`
3. `uv lock --check`
4. `git diff --check`
5. `uv build`, inspect the sdist, and install the wheel in an isolated Python
   3.11 environment.
6. Confirm the GitHub release workflow succeeds and PyPI serves 0.2.39.
7. From Agentdock, confirm `.venv` imports `whyline-relay` 0.2.39 and extracts
   the real paused Grok session id.

## Whyline follow-up

No Whyline runtime code changes are required. Raise Whyline's dependency floor
and lock from `whyline-relay>=0.2.38,<0.3` to `>=0.2.39,<0.3` so a later
`uv sync` cannot restore the broken package. Do that in an isolated Whyline
worktree rather than modifying the currently paused `PANE-1` branch.
