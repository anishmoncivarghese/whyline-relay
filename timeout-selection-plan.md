# Brainstorm per-agent timeout selection

Global constraints:

- The timeout is per agent turn, not a whole-brainstorm deadline.
- The default is 15 minutes; the only choices are 15, 30, 45, and 60 minutes.
- Preserve the existing non-interactive relay timeout configuration and all existing behavior outside interactive brainstorming.
- Do not run real vendor agents in tests; inject deterministic fake runners.
- Codex reviews and commits every task; implementers must not commit.
- Run the focused tests and the complete suite after each task.

- [ ] TO-1: Add configurable brainstorm timeout plumbing
  Replace the fixed interactive brainstorm timeout with an optional per-run
  timeout value passed through chat turns, pass-zero research, review passes,
  and final synthesis. Preserve the current timeout as a compatibility default
  until the setup selector supplies the new 15-minute default. Add focused
  tests proving the selected seconds reach every brainstorm phase.

- [ ] TO-2: Add the timeout selector to brainstorm setup and REPL output
  Extend the brainstorm setup prompts with a clearly labelled per-agent timeout
  menu containing 15 (default), 30, 45, and 60 minutes. Persist the selection
  for a resumed run, pass it through the /brainstorm REPL, and display the
  selected value before execution. Add validation and tests for default,
  explicit choices, invalid input, and the existing agent/failure progress
  table behavior.

- [ ] TO-3: Document and verify the user-facing behavior
  Update the relevant README command/setup documentation with the timeout
  choices and the fact that the value applies separately to each agent turn.
  Run the focused brainstorm suite and the complete relay suite, and verify
  the plan is clean and all tests pass.

