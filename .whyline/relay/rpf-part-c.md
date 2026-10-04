# Relay plan flow, Part C: whyline-relay 0.2.29 (planner questions, named plans, safe config writes)

Every task below is one task of `.whyline/relay/rpf/implementation-plan.md`
(spec: `.whyline/relay/rpf/design-spec.md`). Read that task in full and follow
its steps exactly: write the failing test first, see it fail, implement, then
run the whole suite with `uv run pytest -q`. The plan's "Global Constraints"
apply. Release steps are done by a human afterwards: never push, tag, bump the
version or publish.

- [ ] RPF-8: The planner asks questions and takes answers
  Implement "Task 8: The planner asks questions and takes answers" from the
  plan: planner.PlanQuestions (a loop.Paused carrying questions, stage and
  agent), answer_feedback, answer() re-running the saved stage, questions
  raised again on resume_draft; the fake pipeline agent's "#q1|q2" questions;
  the plan prompts' sentence about putting choices inside each question; the
  {review_feedback} placeholder added to PLAN_REVIEW; and the "## Open
  questions" sentence in brainstorm.PLAN_GENERATION_PROMPT.
  Verify: uv run pytest tests/test_planner_questions.py -q, then
  uv run pytest -q, all passing.

- [ ] RPF-9: Named plan files and config writers
  Implement "Task 9: Named plan files and config writers" from the plan:
  planner.approve(target=...), setup._set_top_level, setup.write_plan,
  setup.write_planner, and write_roles keeping every line of an older config
  except its [roles], [backup] and [status_map] tables (custom [agents.*],
  max_rounds, timeout_minutes, branch_prefix, plan and [planner] survive).
  Verify: uv run pytest tests/test_plan_files.py -q, then uv run pytest -q,
  all passing.
