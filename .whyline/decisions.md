# Decisions

Append-only. Written by whyline; readable without it.

## 2026-09-20 — Late-bind executable lookup and supervise each agent as a child process group

**Actor:** codex
**Role:** implementer
**Task:** RELAY-5

**Because:** runtime lookup keeps tests from accidentally invoking a real vendor CLI, while a surviving parent can tee output and enforce timeouts

**Rejected:**

- Cache shutil.which or exec the agent — cached lookup defeats test patches, and exec would prevent relay routing after the agent exits

**Files:** src/whyline_relay/agents.py

<!-- whyline-event: 66f74d1ef79c47fd89d7a4b859cab760 -->

## 2026-09-20 — RELAY-5 review: request changes. Code matches the plan byte for byte, but the plan's agents.run had two defects, so the plan was amended and the task sent back

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-5

**Because:** Probed with real processes: one non-UTF-8 byte in agent output raised UnicodeDecodeError and left the child running; an agent ignoring SIGTERM outlived a 1s timeout by its full 6s runtime, so an unattended run could stall forever. Fixed with errors=replace and a SIGKILL escalation after KILL_GRACE_SECONDS. Both new tests fail on the old code and pass on the new (9 passed)

**Rejected:**

- Approve and defer the fixes — Task 5 is the module the plan calls riskiest, and both defects break unattended operation, which is the tool's purpose
- Claude edits agents.py itself — the split is Codex develops, Claude reviews; the defect was in the plan, so the plan was fixed and the task re-dispatched
- Reject .whyline/decisions.md as out of scope — it is whyline's own tracked decision log, written by the required note command, and not a stray file

**Files:** src/whyline_relay/agents.py, tests/test_agents.py

<!-- whyline-event: 2e597f237b1d4c7eb9200ccd327d3537 -->

## 2026-09-20 — Make agent timeouts bounded and agent output decoding fault-tolerant

**Actor:** codex
**Role:** implementer
**Task:** RELAY-5

**Because:** an unattended relay must survive invalid UTF-8 and must stop child process groups even when they ignore SIGTERM

**Rejected:**

- Strict UTF-8 decoding and SIGTERM-only timeout handling — either can strand the relay or leave a child running

**Files:** src/whyline_relay/agents.py, tests/test_agents.py

<!-- whyline-event: c9026e73c783452991ad7342768fad33 -->

## 2026-09-20 — RELAY-5 review round 2: approve

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-5

**Because:** agents.py and test_agents.py are byte-identical to the amended, independently verified plan code; suite passes 37/37 with codex and claude tripwire shims on PATH never firing; a 30s SIGTERM-ignoring agent was stopped at 6.0s (1s timeout + 5s grace) with no orphan; a non-UTF-8 byte no longer crashes run(); HEAD unmoved so Codex did not commit

**Rejected:**

- Also require a third review round — no defect remained after the real-process re-probe

**Files:** src/whyline_relay/agents.py, tests/test_agents.py

<!-- whyline-event: 6ff924b590654b38a154b1e3ffd09b8c -->
