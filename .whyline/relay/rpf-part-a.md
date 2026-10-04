# Relay plan flow, Part A: whyline-relay 0.2.28 (recipes, Antigravity trust, failure reasons)

Every task below is one task of `.whyline/relay/rpf/implementation-plan.md`
(spec: `.whyline/relay/rpf/design-spec.md`). Read that task in full and follow
its steps exactly: write the failing test first, see it fail, implement, run
the whole suite with `uv run pytest -q`. The plan's "Global Constraints"
section applies to every task. Release steps are done by a human afterwards:
never push, tag, bump the version or publish.

- [x] RPF-1: Built-in recipes for grok and antigravity
  Implement "Task 1: Built-in recipes for grok and antigravity" from
  .whyline/relay/rpf/implementation-plan.md: create src/whyline_relay/recipes.py,
  apply the recipes in config.load (skipping a name the config already defines,
  including the older [agents.agy] alias), add tests/test_recipes.py, and add the
  README paragraph. Verify: uv run pytest tests/test_recipes.py -q, then
  uv run pytest -q, all passing.

- [ ] RPF-2: Antigravity trust helpers
  Implement "Task 2: Antigravity trust helpers" from
  .whyline/relay/rpf/implementation-plan.md: create src/whyline_relay/antigravity.py
  (settings_path, is_trusted, trust, decline, is_declined, forget_decline,
  SettingsUnreadable, ALLOW), add ".whyline/relay/antigravity-declined" to
  gitcheck.RELAY_IGNORE, and add tests/test_antigravity_trust.py. Tests must point
  HOME/USERPROFILE at a temporary directory; never touch the real
  ~/.gemini/antigravity-cli/settings.json. Verify: uv run pytest -q, all passing.

- [ ] RPF-3: Failures say why
  Implement "Task 3: Failures say why" from
  .whyline/relay/rpf/implementation-plan.md: add Claude's limit wordings to
  agents.RATE_LIMIT_MARKERS, add brainstorm.failure_reason (category plus the
  agent's own last line, printable, at most 160 characters), use it everywhere a
  failed brainstorm turn is reported, and add
  tests/test_brainstorm_failure_reason.py. Verify: uv run pytest -q, all passing.
