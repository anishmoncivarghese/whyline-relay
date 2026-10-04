# Relay guided flow v2: whyline-relay part (0.2.31)

Each task is one task of `.whyline/relay/fv2/implementation-plan.md` (spec:
`.whyline/relay/fv2/design-spec.md`). Read the task in full and follow its
steps exactly: write the failing test first, see it fail, implement, then run
the whole suite with `uv run pytest -q`. The plan's "Global Constraints"
apply. Tests must not depend on installed agent CLIs, must not touch the real
home folder, and must pass on Windows. Never push, tag, bump the version or
publish -- a human releases afterwards.

- [ ] FV2-1: Release role in the relay config
  Implement "Task 1" from the plan: Config.release_role ("human" by default,
  or a known agent), accepted in both config formats without breaking role
  validation; setup.write_release; write_roles keeps an existing release key.
  Verify: uv run pytest -q.

- [ ] FV2-2: The spec pipeline
  Implement "Task 2" from the plan: planner._Kind with PLAN_KIND and
  SPEC_KIND threaded through the planner, state file names, specs.py,
  SPEC_DRAFT and SPEC_REVIEW prompts, RELAY_IGNORE entries. Existing planner
  tests must pass unchanged. Verify: uv run pytest -q.

- [ ] FV2-3: Plans from specs, release marking, synthesis revision
  Implement "Task 3" from the plan: planner.draft(spec=...), the
  `relay-profile: release` rule in PLAN_DRAFT and PLAN_REVIEW,
  brainstorm.final_synthesis and brainstorm.revise_synthesis.
  Verify: uv run pytest -q.

- [ ] FV2-4: The relay commits approved work in every config format
  Implement "Task 4" from the plan: loop.commit_message, the two-role
  approval committing via _commit_and_approve unless the reviewer already
  committed, the REVIEW prompt saying "do not commit", and the golden prompt
  file updated. Verify: uv run pytest -q.

- [ ] FV2-5: Release tasks pause for the human; done and skip
  Implement "Task 5" from the plan: the release pause in _run_plan, the
  release agent path, mark_done / mark_skipped with skipped-tasks.json,
  `whyline relay done` / `skip`, the resume hint, and the preflight warning.
  Verify: uv run pytest -q.
