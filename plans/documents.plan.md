<!-- whyline-plan v1 | source: hand | drafted-by: claude | created: 2026-11-01T18:00:00+05:30 -->
# Named document kinds: PRD, design doc and build record, drafted and reviewed like specs

Runs after `plans/relay-followups.plan.md` (RLY-1..14) is complete and released. Needed by agentdock's `plans/sdlc-pipeline.plan.md` (SDLC-1 waits for this release). Design: agentdock `docs/superpowers/specs/2026-11-01-agentic-sdlc-pipeline-design.md`, section "How it fits the code today".

This file is the plan Relay runs. Each task is one `- [ ] ID: title` item. Detail lines stay indented. Do not add another checkbox line anywhere in this file. Do not implement from this plan until Relay starts the task.

Why:
- `planner` has two private kinds, `PLAN_KIND` and `SPEC_KIND`. The whyline console needs more documents (PRD, design doc, build record), drafted and reviewed by the same machinery: draft, review, revise, answer, resume, failover.
- Every revision today needs a live checkpoint. The console keeps documents in its own versioned set and discards the checkpoint while a person reviews. So it needs to revise a given text with no checkpoint.

Global constraints:
- No real vendor agents in tests; inject fake runners.
- Keep every existing behaviour not named here. `PLAN_KIND`, `SPEC_KIND`, `specs.py` and their checkpoints and file names are unchanged.
- No new dependency.
- Run the focused tests and the complete suite after each task.

- [ ] RDOC-1: Public document kinds and a `documents` module
  Edit `src/whyline_relay/planner.py`, `src/whyline_relay/prompts.py`, a new `src/whyline_relay/documents.py` and their tests only.
  1. Make `_Kind` public as `DocKind(task_id, draft_name, state_name, draft_prompt, review_prompt)`. Keep `_Kind` as an alias. Add `DocKind.named(name)`, which builds `task_id="__<name>__"`, `draft_name="draft-<name>.md"`, `state_name="<name>-state.json"` and prompts `<name>-draft`/`<name>-review`. The checkpoint and draft file are separate for each name.
  2. Add built-in prompts, in the style of `SPEC_DRAFT`/`SPEC_REVIEW`:
     - `prd-draft`/`prd-review`: a product requirements document (what and why, not how). Requirements are numbered R1, R2… The reviewer checks every requirement is testable and that nothing is architecture.
     - `design-draft`/`design-review`: a design document for an architect. It has decision records with options considered. The reviewer checks that the alternatives are real and that the risks, migration and rollback are covered.
     - `build-record-draft`/`build-record-review`: a record of what was built against the approved documents attached. It is written only from the attached inputs, never invented. The reviewer checks every claim against the attachments.
  3. Add constants `PRD_KIND`, `DESIGN_KIND` and `BUILD_RECORD_KIND` built with `DocKind.named`.
  4. In `documents.py`, add `draft`, `revise`, `answer`, `resume_draft`, `discard`, `pending_description` and `draft_path`. Each takes `kind: DocKind` and delegates to `planner` with that kind, as `specs.py` does for `SPEC_KIND`. A spec is `documents` with `SPEC_KIND`. Re-export `DocKind`.
  5. `_announcer` names the document from the kind: "the PRD", "the design", "the build record", "the spec", "the plan".
  Tests:
  - two kinds hold checkpoints at once
  - the prompts resolve and a `.whyline/relay/prompts/prd-draft.md` override wins
  - draft and revise round trips with a fake runner, for PRD and design

- [ ] RDOC-2: A template, and revising a given text
  Edit `src/whyline_relay/planner.py`, `src/whyline_relay/documents.py` and their tests only.
  1. `draft(..., template: str = "")`, on `planner.draft` and `documents.draft`, appends `\n\nUse this structure:\n\n<template>` to the draft prompt. It is saved in the checkpoint so revise, answer and resume keep it. An empty template changes nothing.
  2. Add `documents.revise_text(root, settings, kind, text, feedback, *, description, template="", attachments=(), print_fn=None, runner=subprocess.run) -> Path`:
     1. It refuses with `PlanAlreadyInProgress` when that kind already has a checkpoint.
     2. It writes `text` to the kind's draft path.
     3. It saves a checkpoint with `description`, `template` and `attachments`.
     4. It runs the same pipeline `revise` runs, with `feedback`. The draft prompt must say to start from the existing draft file and change it, not rewrite from nothing. Add that sentence when the draft file is not empty.
     5. It returns the draft path. The checkpoint stays until the caller discards it, as with `draft`.
  3. For `PLAN_KIND` with a Test planner set, `revise_text` also runs the tests stage, so the test plan stays in step. When an existing test plan is passed as `tests_text`, it is seeded beside the draft first.
  Tests:
  - the template is in the prompt and survives revise
  - `revise_text` seeds the text and the prompt says to edit it
  - the refusal when a checkpoint exists
  - the plan kind regenerating tests

- [ ] RDOC-3: Publish whyline-relay with RDOC-1..2
  relay-profile: release
  Bump the version in `pyproject.toml`, `src/whyline_relay/__init__.py` and `uv.lock` to the next patch version (0.2.41 if RLY-14 released 0.2.40). Write `docs/releases/v<version>.md` from every task since the previous release, in user terms. Run `uv run pytest -q` with the agent CLIs off PATH and `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`, and again on Python 3.11. Push, wait for green CI, then tag `v<version>`, push the tag and let the release workflow publish to PyPI. Afterwards run `uv --no-cache tool upgrade whyline` and confirm that `whyline-relay --version` shows the new version. Then, in agentdock, mark SDLC-1 done when that plan reaches it.
