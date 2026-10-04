# Console attachments: whyline-relay part (0.2.30)

Each task is one task of `.whyline/relay/att/implementation-plan.md` (spec:
`.whyline/relay/att/design-spec.md`; the spike's results:
`.whyline/relay/att/capabilities.md`). Read the task in full and follow its
steps exactly: write the failing test first, see it fail, implement, then run
the whole suite with `uv run pytest -q`. The plan's "Global Constraints"
apply. Tests must not depend on which agent CLIs are installed. Never push,
tag, bump the version or publish -- a human releases afterwards.

- [x] ATT-2: The attachments module
  Implement "Task 2: The `attachments` module" from the plan: create
  src/whyline_relay/attachments.py (Delivery, kind_of, delivery with the
  spike-verified table, prompt_block, command_with_images using one
  `--image=<path>` per image for codex and never bare -i) and
  tests/test_attachments.py. Verify: uv run pytest tests/test_attachments.py -q,
  then uv run pytest -q.

- [x] ATT-3: Chat turns and brainstorm passes take attachments
  Implement "Task 3" from the plan: chat.run_turn and _execute_agent_call take
  attachments (prompt block appended, codex image flags added, prompt still
  last), and run_pass_zero, run_review_pass, run_final_synthesis and
  generate_plan_from_synthesis pass them to every turn. Add
  tests/test_chat_attachments.py. Verify: uv run pytest -q.

- [x] ATT-4: The planner keeps attachments
  Implement "Task 4" from the plan: PlanState.attachments (default empty, old
  checkpoints still load), loop.run_agent(attachments=...), and planner.draft
  saving them so revise, resume_draft and answer reuse them. Add
  tests/test_planner_attachments.py. Verify: uv run pytest -q.
