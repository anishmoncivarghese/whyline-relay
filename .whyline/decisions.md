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

## 2026-09-20 — Verify task completion from Git state, not agent claims

**Actor:** codex
**Role:** implementer
**Task:** RELAY-6

**Because:** a task is complete only when HEAD advances beyond its recorded base and the new commit message names the task id; branch and dirty-tree checks likewise query Git directly

**Rejected:**

- Trust the handoff status alone — it could approve without a corresponding attributable commit

**Files:** src/whyline_relay/gitcheck.py, tests/test_gitcheck.py

<!-- whyline-event: dc95ece18a854537a8d9b3f973efff9c -->

## 2026-09-20 — RELAY-6 review: request changes. Code matches the plan byte for byte, but commit_verified used a substring test, so a commit for RELAY-10 verified RELAY-1

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-6

**Because:** Probed in real repos: commit naming only RELAY-10 -> commit_verified(RELAY-1) returned True. This plan has 13 tasks, so a stray commit for Task 10-13 could tick Task 1 and break spec 5.4 (a ticked box always corresponds to a real commit). Fixed with a whole-id regex (_names_task); 7 new parametrized cases, 3 fail on the old code, all 14 pass on the new. A second suspected defect (strict UTF-8 decode of git output) was probed and does not exist: git 2.50 re-encodes non-UTF-8 messages on output

**Rejected:**

- Approve and defer — the false positive lives in the one function whose job is to prevent false ticks
- Require a delimiter convention on task ids instead — the ids come from the user's plan and cannot be constrained

**Files:** src/whyline_relay/gitcheck.py, tests/test_gitcheck.py

<!-- whyline-event: a23f505a5c524aafba23d670d539d656 -->

## 2026-09-20 — Match task ids as whole identifiers when verifying commits

**Actor:** codex
**Role:** implementer
**Task:** RELAY-6

**Because:** substring matching lets a commit for RELAY-10 falsely verify RELAY-1, while escaped boundary-aware matching preserves punctuation and dotted ids without constraining user-defined task names

**Rejected:**

- Keep task_id in message — prefix collisions can tick the wrong plan item
- Require fixed delimiters around task ids — plan authors control ids and valid messages use varied punctuation

**Files:** src/whyline_relay/gitcheck.py, tests/test_gitcheck.py

<!-- whyline-event: cf2c8f4f3cd74a3c8c1bf22b405cc585 -->

## 2026-09-20 — RELAY-6 review round 2: approve

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-6

**Because:** gitcheck.py and test_gitcheck.py are byte-identical to the amended, independently verified plan code; suite passes 51/51 with codex and claude tripwire shims never firing; 12 real-repo boundary probes on the repo code all correct (RELAY-10 no longer verifies RELAY-1; dotted, punctuated, regex-metacharacter ids and HEAD-unmoved all right); HEAD unmoved so Codex did not commit. Matching is case-sensitive and rejects longer hyphenated ids such as RELAY-1-2, both of which fail toward pausing, not toward a false tick

**Rejected:**

- Make the match case-insensitive — it widens what counts as verification, and the relay's own prompt tells the agent to write the id verbatim

**Files:** src/whyline_relay/gitcheck.py, tests/test_gitcheck.py

<!-- whyline-event: 1149bc3beb08414db529fce4ef4531e1 -->
