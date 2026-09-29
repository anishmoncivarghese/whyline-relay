# Brainstorm agent progress and failure reporting

Global constraints:

- Preserve existing brainstorm artifacts, failover behavior, and plan-source flow.
- Every selected agent gets a visible start and terminal status before the next phase continues.
- Runtime failures must not be treated as successful records.
- Failed agents must not be presented as successful research or selected for final synthesis.
- Emit structured progress events in addition to human-readable output so the future TUI can render a table.
- Do not run real vendor agents in tests; inject runners and deterministic fake results.
- Every existing test must pass after every task.

- [x] PRG-1: Add structured brainstorm progress events and lifecycle reporting
  Add a small event/status contract for `starting`, `running`, `succeeded`,
  `failed`, and `skipped` agent turns. Pass an optional progress callback
  through pass-zero, review passes, and final synthesis. Print a clear CLI
  line before each agent starts and after it finishes, including ordinal,
  elapsed time, and the agent label. Keep output capture and relay logs intact.
  Add focused tests for callback order, elapsed-time formatting, and the
  human-readable start/finish lines.

- [x] PRG-2: Classify runtime failures and exclude unsuccessful research
  Classify quota/rate-limit, timeout, authentication, permission, missing
  executable, and generic non-zero failures from the `run_turn` record and
  exceptions. Make pass-zero print the reason, record the failed agent in its
  status map, and omit it from the successful-research map used for merge and
  later review. Preserve successful substitutions from the backup chain.
  Add tests for Claude quota responses, Codex timeout, missing agents, and
  generic failures.

- [x] PRG-3: Select a viable final synthesizer after failures
  If the requested final agent fails at runtime, choose the first successful
  selected agent with usable research and announce the substitution. If no
  selected agent succeeds, stop with an actionable message and do not create
  an empty synthesis. Ensure review passes skip failed agents while preserving
  successful sections and actual-agent attribution. Add focused tests for
  final-agent fallback and the all-agents-failed case.

- [x] PRG-4: Verify CLI progress output and future-TUI event compatibility
  Wire the event stream through the `/brainstorm` REPL and render a compact
  progress table showing agent, phase, state, elapsed time, and failure reason.
  Keep the event payload serializable and stable for the future full-screen
  TUI. Run the focused brainstorm suite and the complete relay suite, then
  document the exact commands and results in the handoff.

## Final check

Run the full suite once more. Manually exercise a four-agent brainstorm with
one unavailable/quota-limited agent and one timeout, confirming that the
terminal reports every agent's start and finish state, merges only successful
research, and selects a viable final synthesizer.
