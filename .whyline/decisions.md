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

## 2026-09-20 — Keep routing pure and make M1 dry-run assemble without launching

**Actor:** codex
**Role:** implementer
**Task:** RELAY-7

**Because:** routing must depend only on the handoff event and configured statuses, while dry-run should expose the exact next task, argv, sync packet, and prompt without spending agent quota

**Rejected:**

- Infer success from process exit or output — the spec makes the whyline handoff record the sole routing authority
- Invoke the agent during dry-run — that would violate the milestone's no-launch contract

**Files:** src/whyline_relay/routing.py, src/whyline_relay/cli.py, tests/test_routing.py, tests/test_cli_dryrun.py

<!-- whyline-event: 2bf0d947204b4efcb05c47870d53b8bb -->

## 2026-09-20 — RELAY-7 review: request changes. Code matches the plan byte for byte, but decide() ignored to_actor, contradicting the spec's (to_actor, status) routing table

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-7

**Because:** Spec 5.2 routes codex+assigned/changes-requested to implement, claude+ready-for-review to review, and only approved/blocked for any recipient; anything else pauses. Probed: codex+ready-for-review, claude+changes-requested and claude+assigned all routed to an agent instead of pausing. A mis-addressed handoff means a confused agent, and every later task builds on decide(). Fixed with IMPLEMENTER/REVIEWER checks; 3 new parametrized cases fail on the old code, all 15 routing and CLI tests pass on the new, 66 total. Dry run verified side-effect free and launching no agent (tripwire shims not fired, HEAD and .whyline unchanged). Deferred to the Task 10 review, not this round: (1) start --only with an unknown id prints 'Nothing to do' and exits 0 in this cmd_start, which Task 10 replaces, so check run_plan for it; (2) start --dry-run on a repo where whyline is not initialised raises a raw WhylineUnavailable traceback, while Task 10's guard catches it for real runs

**Rejected:**

- Fix the --only silent success in this round — cmd_start is rewritten in Task 10, so a patch here is churn and risks plan errors
- Approve and defer the to_actor check — decide() is the one place every relay decision passes through, and the plan header says the spec wins where they disagree

**Files:** src/whyline_relay/routing.py, tests/test_routing.py

<!-- whyline-event: 97d6fd8c157b44298d9e4124e8b37bd6 -->

## 2026-09-20 — Route actionable statuses only when addressed to the matching agent

**Actor:** codex
**Role:** implementer
**Task:** RELAY-7

**Because:** the spec defines routing over the (to_actor, status) pair, and launching on a mis-addressed handoff would amplify agent confusion instead of pausing safely

**Rejected:**

- Route on status alone — codex plus ready-for-review and claude plus assigned or changes-requested would launch the wrong role

**Files:** src/whyline_relay/routing.py, tests/test_routing.py

<!-- whyline-event: 8d02428361e74302b64616b4bdf48893 -->

## 2026-09-20 — RELAY-7 review round 2: approve (milestone M1)

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-7

**Because:** routing.py and test_routing.py are byte-identical to the amended, independently verified plan code; cli.py and test_cli_dryrun.py unchanged from the plan; suite passes 66/66 with codex and claude tripwire shims never firing; 11 (to_actor,status) cases on the real code match spec 5.2; the fake agent's handoffs and all three real prompt handoffs are compatible with the stricter decide(); the dry run launches no agent and leaves HEAD and the working tree untouched; HEAD unmoved so Codex did not commit. Deferred to the Task 10 review: unknown --only id exits 0 with 'Nothing to do', and --dry-run without whyline init raises a raw traceback

**Rejected:**

- Require the deferred CLI items in this round — cmd_start is rewritten in Task 10, so patching it here is churn

**Files:** src/whyline_relay/routing.py, tests/test_routing.py, src/whyline_relay/cli.py

<!-- whyline-event: f5996c6618f04cc3bec5e7adecc91d69 -->

## 2026-09-20 — Drive M2 from task-scoped handoffs and resume at the recorded decision point

**Actor:** codex
**Role:** implementer
**Task:** RELAY-8

**Because:** the relay must reject handoffs for another task, route valid resumed review or approval states without rerunning Codex, and keep its runtime logs out of reviewer commits while preserving handoffs as the sole authority

**Rejected:**

- Restart every resumed task with Codex — it repeats completed work and contradicts same-decision-point recovery
- Route any fresh handoff regardless of task id — a confused or stale event could send the wrong work to Claude
- Gitignore all of .whyline/relay — config and prompt templates are intended to remain reviewable and committable

**Files:** src/whyline_relay/loop.py, src/whyline_relay/gitcheck.py, tests/test_loop_single.py

<!-- whyline-event: fbaba918b7e8476ea7363128981dce35 -->

## 2026-09-20 — RELAY-8 review: approve (milestone M2)

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-8

**Because:** loop.py, test_loop_single.py, fake_agent.py, cli.py and test_gitcheck.py are byte-identical to the pre-reviewed plan blocks; gitcheck.py differs only in placing the RELAY_IGNORE constant at the top of the module instead of just above the function, which is behaviour-neutral. Suite passes 82/82 with codex and claude tripwire shims never firing. Ran the real whyline-relay CLI end to end in a throwaway repo with the real whyline and fake agents: relay/plan was created, real whyline claim and sync worked, the handoff routed, the commit verified and the box ticked, and the relay's own logs stayed out of the task commit. HEAD unmoved so Codex did not commit. Fake-agent coverage does not prove the real codex and claude CLIs: the owner-watched M2 run is still required before Task 9

**Rejected:**

- Require Codex to move RELAY_IGNORE below the imports as the plan wrote it — cosmetic, no behaviour difference

**Files:** src/whyline_relay/loop.py, src/whyline_relay/gitcheck.py, src/whyline_relay/cli.py, tests/fake_agent.py

<!-- whyline-event: 9359f7ab7d9a43dfaeb8573f6227c680 -->

## 2026-09-20 — Enforce the developer/reviewer boundary at runtime and carry Claude permissions explicitly

**Actor:** codex
**Role:** implementer
**Task:** RELAY-8b

**Because:** the real M2 run showed Codex can commit under workspace-write and Claude ignores untrusted project allowlists, so the relay must detect Codex HEAD movement and pass a dedicated settings file; no-handoff diagnostics can explain denials without influencing routing

**Rejected:**

- Trust the Codex sandbox to protect Git — codex-cli 0.155.1 demonstrably committed under workspace-write
- Write only .claude/settings.json — claude -p ignores its allow entries until interactive workspace trust
- Route from permission-denial output — handoffs remain the sole routing authority

**Files:** src/whyline_relay/loop.py, src/whyline_relay/config.py, tests/test_loop_single.py

<!-- whyline-event: a6ce040772ee428d98c9a451a1311f5f -->

## 2026-09-20 — RELAY-8b review: approve

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-8b

**Because:** loop.py, config.py, fake_agent.py, test_loop_single.py and test_config.py are byte-identical to what the plan's literal Task 8b instructions produce on the committed Task 8 tree; 22 and 86 tests pass as the plan states, with codex and claude tripwire shims never firing; HEAD unmoved so Codex did not commit. Step 7 real-CLI re-check passed against the real codex and claude with the default command carrying --settings: relay/plan created, Codex implemented without committing, Claude committed hello.py and decisions.md with the trailer and no relay logs, handoff approved, exit 0. The Codex commit guard itself is proven with the fake agent's real git commit; a real Codex was measured able to commit (probe commit 99411a5) but I did not induce it inside the relay, since obeying a task that contradicts the prompt tests obedience, not the guard. Codex took about 5 minutes on one turn against under 1 minute earlier: variance, but note the 30 minute default timeout exists for it

**Rejected:**

- Also force a real Codex commit inside the relay — the guard is a deterministic HEAD comparison already proven with a real git commit by the fake agent

**Files:** src/whyline_relay/loop.py, src/whyline_relay/config.py, tests/fake_agent.py

<!-- whyline-event: 99a0bfa875c8424ab8708cb268ad60c4 -->
## 2026-09-20 — Commit each verified plan tick mechanically and save exact resume progress

**Actor:** codex
**Role:** implementer
**Task:** RELAY-9

**Because:** an uncommitted tick otherwise contaminates the next task's review commit or remains dirty at the end, while saved base, round and handoff identity let resume continue without replaying completed agent work

**Rejected:**

- Leave plan ticks uncommitted — the next reviewer commit would absorb the previous tick and the final run would end dirty
- Let the relay commit arbitrary leftovers — only plan.md is mechanical relay-owned state; code remains Claude-reviewed

**Files:** src/whyline_relay/loop.py, src/whyline_relay/state.py, src/whyline_relay/gitcheck.py

<!-- whyline-event: 316db8f3aaad41418dac861cf25ae8bf -->


## 2026-09-20 — RELAY-9 review: approve

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-9

**Because:** loop.py, state.py, gitcheck.py and all Task 9 tests are byte-identical to the pre-reviewed plan blocks (Codex's gitcheck.py differs from a from-scratch build only in the RELAY_IGNORE placement approved in Task 8). run_plan re-reads the plan before ticking, raises PlanError on an unknown --only, reuses the saved base commit, round and last_handoff_id on resume, saves state on Paused and KeyboardInterrupt, checks nothing else is uncommitted after an approval, then ticks and commits only the plan file. 104 passed, matching Codex's report for this task, with codex, claude and osascript tripwires never firing. Reviewed as one of five tasks Codex built in one continuous uncommitted run: each task's tree was rebuilt from the plan and its test count cross-checked against Codex's per-task report

**Files:** .whyline/decisions.md

<!-- whyline-event: abe3f06da0164f5cb967e19937de7a6e -->
