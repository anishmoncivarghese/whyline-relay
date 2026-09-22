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
## 2026-09-20 — Guard the branch agents will commit to and resume onto the saved branch

**Actor:** codex
**Role:** implementer
**Task:** RELAY-10

**Because:** starting from main is normal when the relay will switch branches, while resuming on whichever branch happens to be checked out could bypass the main-branch guard and commit in the wrong place

**Rejected:**

- Guard the current branch before switching — it rejects the normal main-to-relay flow and makes --branch ineffective
- Resume in the current checkout — a paused run's branch is durable state and must be restored

**Files:** src/whyline_relay/cli.py, tests/test_cli_commands.py

<!-- whyline-event: 47415303dd84426cb89c4e603f64fbb9 -->


## 2026-09-20 — RELAY-10 review: approve

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-10

**Because:** cli.py (assembled from the plan's prose edits) and test_cli_commands.py match the pre-reviewed plan. The guard checks the branch the agents will commit to, so starting from main switches to relay/<plan> and --branch lifts the refusal; resume returns to the saved branch, reuses the saved base commit and --only, and reports git, whyline and plan errors instead of a traceback; an unknown --only or a malformed plan is a clear error with exit 1; status and stop work. 118 passed, matching Codex's report for this task, tripwires never firing. Codex's own cli.py orders two helper functions differently from the plan's assembly; that reordering lands in the Task 12 commit, which takes Codex's file as the final version

**Files:** .whyline/decisions.md

<!-- whyline-event: 0edede732b86471181dbb4dc0d9e81f7 -->
## 2026-09-20 — Treat every agent-run unwind as process-group teardown and persist Ctrl+C state

**Actor:** codex
**Role:** implementer
**Task:** RELAY-11

**Because:** agents run in separate sessions, so SIGINT does not reach them automatically; the relay must terminate then escalate the child group before propagating interruption, while run_plan saves the exact task state for resume

**Rejected:**

- Assume Ctrl+C reaches child agents — start_new_session isolates them and leaves an orphan editing the repository
- Use pgrep or pkill in tests — Codex's sandbox cannot list processes, so a recorded PID and os.kill(pid, 0) provide a meaningful check

**Files:** src/whyline_relay/agents.py, src/whyline_relay/cli.py, tests/test_interrupt.py

<!-- whyline-event: 02cee849701f4a388786fd4e702f00e9 -->


## 2026-09-20 — RELAY-11 review: approve

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-11

**Because:** agents.run now stops the agent's whole process group on any unwind (SIGTERM, then SIGKILL after the grace period) and run_plan saves state on Ctrl+C, so resume works and no orphan is left; notify.py is best-effort and never fatal. tests/conftest.py stubs the CLI notifier so the suite makes 0 real osascript calls (measured with a tripwire; it made 3 before). The interrupt test records the agent's pid in a file and checks it with os.kill(pid, 0), because pgrep and pkill fail inside Codex's sandbox and a pgrep-based test would pass vacuously there; it fails without the kill-on-interrupt fix. 125 passed, matching Codex's report, tripwires never firing

**Files:** .whyline/decisions.md

<!-- whyline-event: 9d77d1ee58dd416f8a3dbb01f6191aca -->
## 2026-09-20 — Keep unattended Claude permissions relay-owned and explicit

**Actor:** codex
**Role:** implementer
**Task:** RELAY-12

**Because:** Claude -p can ignore project permissions before interactive trust, while --settings reliably applies the relay configuration without changing user settings

**Rejected:**

- .claude/settings.json — merging into user-owned settings is both invasive and unreliable for a fresh unattended checkout

**Files:** src/whyline_relay/init.py

<!-- whyline-event: c125e35c0bb646468ce09852eff9b317 -->


## 2026-09-20 — RELAY-12 review: approve, with README corrections

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-12

**Because:** init.py and test_init.py are byte-identical to the pre-reviewed plan blocks. init writes a relay-owned .whyline/relay/claude-settings.json (passed to Claude with --settings; the user's .claude/settings.json is left alone), prompt templates, config and a relay .gitignore; declining or having no terminal writes nothing; it says plainly that the allowlist is not a sandbox. cli.py is Codex's final file, differing from the plan's prose assembly only in helper ordering. The README was corrected by the reviewer (deviation from the strict developer-only split, disclosed in the commit): missing requirements, a worked example that fails on a fresh project, an undisclosed tick commit, and the Codex commit caveat. The spec link is correct for the origin remote but 404s until agentdock is pushed. 138 passed, matching Codex's report, tripwires never firing

**Files:** .whyline/decisions.md

<!-- whyline-event: 926633e8dd1e41c0be77ecd4f6872d2e -->
## 2026-09-20 — Guard executable push paths without outlawing the explicit push deny rule

**Actor:** codex
**Role:** implementer
**Task:** RELAY-13

**Because:** The source must declare Bash(git push:*) as denied while still proving no implementation path invokes git push

**Rejected:**

- Ban every git push string — that would force removal of the safety deny declaration

**Files:** tests/test_no_bypass.py

<!-- whyline-event: f4c612d9b0d94f1fae3152ba7f97de63 -->

## 2026-09-20 — RELAY-13 review: approve

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-13

**Because:** LICENSE is byte-identical to agentdock's Apache-2.0 text. test_no_bypass exempts permission-declaration lines and forbids executed pushes and bypass flags; checked to fail on injected git push and --dangerously calls. Built outside Codex's sandbox: the wheel holds all 14 modules, the LICENSE and the whyline-relay entry point, with License-Expression Apache-2.0 and Requires-Python >=3.11; the sdist adds the tests. 140 passed, matching Codex's final report, tripwires never firing. Step 7 (recording decisions in agentdock) is Claude's and is done separately

**Files:** .whyline/decisions.md

<!-- whyline-event: b9e382fc80d74143b8b5f0e58b1e2ec3 -->

## 2026-09-21 — Remove relay symlinks as links and reject parent-path escapes

**Actor:** codex
**Role:** implementer
**Task:** RELAY-14

**Because:** the remove command must delete the relay setup without ever traversing a symlink to content outside the repository

**Rejected:**

- Follow symlink targets during counting or deletion — that could inspect or delete files outside the repository

**Files:** src/whyline_relay/remove.py, src/whyline_relay/gitcheck.py

<!-- whyline-event: 0e264fd9330f4a93a0052461f26e1abc -->

## 2026-09-21 — RELAY-14 review: approve. remove matches the spec; accepted the non-atomic rmtree-then-exclude order and a stray-file edge case as known, non-blocking limits

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-14

**Because:** Probed with the real CLI in a scratch repo: init then remove leaves .whyline/decisions.md and AGENTS.md alone, a paused run is refused without --force, a second run reports nothing to remove, a .whyline symlink pointing outside the repo is refused, and other exclude lines and CRLF endings survive byte for byte. The rmtree-then-exclude edit is not atomic, but a rerun still strips leftover exclude lines because the nothing-to-remove check needs both to be absent. A regular file at .whyline/relay raises NotADirectoryError from rmtree, which is harmless (nothing is lost, and unlinking it by hand fixes it) and not worth another round

**Rejected:**

- Request changes for the regular-file crash — a stray file at that path is not something init or start creates, so the cost of a round outweighs the benefit
- Make removal atomic across the directory and the exclude file — the filesystem has no transaction, and idempotent rerun already recovers

**Files:** src/whyline_relay/remove.py, src/whyline_relay/gitcheck.py

<!-- whyline-event: d010d19ead684312b1d4af4b25ab081b -->

## 2026-09-21 — Inspect argparse subparsers to enforce help on future options

**Actor:** codex
**Role:** implementer
**Task:** RELAY-15

**Because:** walking the parser choices automatically covers every current subcommand and makes newly added undocumented options fail the suite

**Rejected:**

- Hard-code the current option names — that could let a future undocumented option pass unnoticed

**Files:** tests/test_cli_commands.py

<!-- whyline-event: af49e17cf6f44387ab815b859e58240d -->

## 2026-09-21 — RELAY-15 review round 1: approve help text for every option plus the parser-walking test

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-15

**Because:** all 18 options across start/resume/status/stop/init/remove now carry a one-sentence help matching the task's wording; option names, dests, defaults, types and metavars are unchanged (HEAD and working-tree parsers list identical options); help renders without argparse errors; suite passes 154/154; blanking one help in-process makes the new test's predicate report 'remove --force', so a future undocumented option fails the suite. Defaults quoted in help match the code: branch is branch_prefix + plan stem, and --plan, --max-rounds and --timeout fall back to config

**Rejected:**

- Require the --branch help to say 'branch_prefix from config' — the task specified relay/<plan-name> and that is the built-in default, so the shorter wording is accepted

**Files:** src/whyline_relay/cli.py, tests/test_cli_commands.py

<!-- whyline-event: f85ca1affdfa4909903edba5372ba1ac -->

## 2026-09-21 — Resolve the global version option from installed distribution metadata

**Actor:** codex
**Role:** implementer
**Task:** RELAY-16

**Because:** The argparse version action exits successfully before required subcommand validation, while PackageNotFoundError maps precisely to the specified unknown fallback

**Rejected:**

- Read whyline_relay.__version__ — it could drift from installed package metadata and would make cli.py depend on a second version source

**Files:** src/whyline_relay/cli.py, tests/test_version.py

<!-- whyline-event: 27ee9be944d24474ac137ae01b8f8eb3 -->

## 2026-09-21 — RELAY-16 review round 1: approve --version backed by importlib.metadata with unknown fallback, plus drift test

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-16

**Because:** cli.py reads metadata.version('whyline-relay') and maps PackageNotFoundError to 'unknown', with no hard-coded copy; argparse's version action prints 'whyline-relay <version>' to stdout and exits 0 before the required-subcommand check; main() builds the parser per call so the fallback monkeypatch takes effect; the help text is present and the RELAY-15 parser-walking test already includes top-level actions; test_module_version_matches_package_metadata fails if __init__.__version__ and package metadata diverge; stdlib only. I could not run the suite in the review session (command execution was blocked), so the implementer's reported 158 passed and the --version run are taken on trust

**Rejected:**

- Request removing test_version_option_has_help as redundant with the RELAY-15 help test — it is harmless, cheap and pins the requirement locally, so not worth a round trip

**Files:** src/whyline_relay/cli.py, tests/test_version.py

<!-- whyline-event: 96f6189f81da4743926005bb49111336 -->

## 2026-09-21 — Classify the relay path itself as the removable file when it is not a directory

**Actor:** codex
**Role:** implementer
**Task:** RELAY-17

**Because:** This makes regular and special non-directory leftovers visible to the count and tracked-file report, then removes them safely with unlink while preserving directory and symlink behavior

**Rejected:**

- Catch NotADirectoryError from shutil.rmtree — it would report the wrong file count and fail only after confirmation instead of selecting the correct removal operation up front

**Files:** src/whyline_relay/remove.py, tests/test_remove.py

<!-- whyline-event: 487ae7791a804b1596f7df9fc0889099 -->

## 2026-09-21 — RELAY-17 review: approve. remove now treats a non-directory .whyline/relay as the removable file (count, list, confirm, unlink) and exits 0

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-17

**Because:** Read the diff: relay_is_directory is computed once (not symlink and is_dir) and drives the state.json refusal, the listing and the rmtree-versus-unlink choice; symlinks are still classified and unlinked before any directory check, so nothing outside the repo is followed, and the existing Directory to remove message is kept for directories and symlinks. The four requested cases (--yes, y, declined, tracked report) each assert no Traceback, and the full suite passes (162). Accepted that FIFO/socket entries share the unlink branch without a dedicated test: the branch is identical to the regular-file one and creating FIFOs in tests is platform-fragile. My scratch-repo CLI probes were blocked by the sandbox, so verification is by code reading plus the test suite

**Rejected:**

- Request a FIFO test — same unlink branch as the regular file, low value against a platform-dependent test

**Files:** src/whyline_relay/remove.py, tests/test_remove.py

<!-- whyline-event: 5257f6fbc1f74be5ba0f676bc235cdea -->

## 2026-09-21 — Keep progress output terminal-only and drive heartbeats from streamed output activity

**Actor:** codex
**Role:** implementer
**Task:** RELAY-18

**Because:** The loop owns turn and plan context, while agents.run owns child output and lifetime; a condition-based heartbeat there can reset on every line, stop and join at process exit, and share a terminal lock without contaminating agent logs

**Rejected:**

- Write progress through the log tee — relay status would enter agent log files and violate the terminal-only requirement
- Use repeating timers without activity tracking — heartbeats would continue after fresh agent output or race beyond process completion

**Files:** src/whyline_relay/agents.py, src/whyline_relay/loop.py

<!-- whyline-event: c317a1475993417b92c6140a9a779ebc -->

## 2026-09-21 — Approve RELAY-18 progress lines and heartbeat as implemented; accept 'finished' line also printing when a turn ends by timeout, missing binary or interrupt

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-18

**Because:** Start/finish/tick lines and the echo-gated, output-resetting heartbeat match the spec, stay out of the log files, and the thread is stopped and joined in finally with no lock-order deadlock. The finish line sits in a finally so every turn that printed a start line gets a matching end; RELAY-19 is planned to make status truthful, so the wording for failed turns can be refined there

**Rejected:**

- Request a test that output resets the heartbeat — the reset logic is small and the required cases (fires, stops at agent end, silent with echo=False, absent from logs) are covered, so this is not worth another round
- Print the finish line only on success — it would leave a timed-out turn with a start line and no end, which is more ambiguous on a quiet terminal

**Files:** src/whyline_relay/agents.py, src/whyline_relay/loop.py

<!-- whyline-event: 31f9aa9764f24fd4b5f66cc110746ba8 -->

## 2026-09-21 — Use an atomic, process-owned live marker across the whole plan run

**Actor:** codex
**Role:** implementer
**Task:** RELAY-19

**Because:** A fully written temporary file hard-linked into place lets only one relay claim a repository, per-turn replacement refreshes the status fields, and owner-aware finally cleanup covers completion, pauses, interrupts and exceptions without deleting another process's marker

**Rejected:**

- Only check running.json in the CLI — two simultaneous starts could both pass before either writes the marker
- Remove the marker after each agent turn — that would expose gaps between turns and tasks where a second relay could enter

**Files:** src/whyline_relay/running.py, src/whyline_relay/loop.py

<!-- whyline-event: 9f8c158525af46209682df858e88f263 -->

## 2026-09-21 — Approve RELAY-19 live marker as implemented; accept the narrow stale-takeover race and first-turn marker timing

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-19

**Because:** Tests pass (181). Marker is hard-linked atomically, refreshed per turn, cleared owner-aware in run_plan/run_task finally blocks; status, start, resume and remove (even --force) behave per spec and the exclude line is picked up by existing repos. Residual risks are narrow: two relays racing over a stale marker could both take over (the loser unlinks the winner's fresh file), the marker first appears at the first agent turn rather than at CLI start, and a corrupt marker with non-UTF-8 bytes or an out-of-range pid would raise from read()/os.kill instead of being treated as stale

**Rejected:**

- Request changes for the stale-takeover race — needs two starts within milliseconds after a crash, and the loser of the atomic link still cannot corrupt a live run's marker in the common case
- Request changes for hardening read()/pid_is_alive against hand-corrupted files — only the relay writes this file, atomically

**Files:** src/whyline_relay/running.py

<!-- whyline-event: 8641dbbcef754ee28bc56c7040a0624c -->

## 2026-09-21 — Route denied reviewer test commands to a distinct blocked outcome

**Actor:** codex
**Role:** implementer
**Task:** RELAY-20

**Because:** A permission denial needs human allowlist action and must prevent approval without being misreported as an implementation defect

**Rejected:**

- Use changes-requested — the implementer cannot fix a reviewer command permission denial in the code under review

**Files:** src/whyline_relay/prompts.py

<!-- whyline-event: d084d4e4c08d4eaa871c018fe93aafbb -->

## 2026-09-21 — RELAY-20 review: approve

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-20

**Because:** REVIEW template now requires the reviewer to run the plain test command itself, forbids env prefixes/pipes/chains, routes a denied command to a blocked handoff with a --question naming the exact command and permission, and exempts no-code/no-test tasks via the summary. Placeholders unchanged; init adds .venv/bin/pytest and .venv/bin/python to the python preset and tail/head/wc/grep/ls to the base allowlist; DENY untouched. Tests cover each required phrase and both allowlists; uv run pytest -q passes (185 tests). Residual risk accepted: loop.py's Paused message for a blocked handoff prints only the summary, not the --question, so the denied command is visible only in the handoff record unless the reviewer repeats it in the summary

**Files:** src/whyline_relay/prompts.py

<!-- whyline-event: d40ac05d302a479d9e046175c517fb60 -->

## 2026-09-21 — Render and then cap blocked pause reasons at 300 characters

**Actor:** codex
**Role:** implementer
**Task:** RELAY-21

**Because:** Capping the complete message preserves the unchanged no-question wording, keeps questions ordered, and guarantees the status and notification limit

**Rejected:**

- Cap each question separately — several individually capped questions could still exceed the total limit

**Files:** src/whyline_relay/loop.py

<!-- whyline-event: e8f396593ed741d29927bbbd622c5bf5 -->

## 2026-09-21 — Approve RELAY-21: blocked pause reason lists each handoff question, capped at 300 chars

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-21

**Because:** Meets the spec: Handoff.questions defaults to (), read() filters to string items, no-question wording is unchanged (asserted by exact match), cap applies to the whole rendered reason (297 chars + '...'), and status output is covered by a test. uv run pytest -q gives 190 passed. Accepted risk: a long summary can push later questions out of the 300-char window, which is inherent in the requested whole-reason cap

**Rejected:**

- Request reserving room for the first question — the task specified a cap on the whole reason and said to change nothing else

**Files:** src/whyline_relay/loop.py

<!-- whyline-event: 9f8bdacaf0d443998f65f92c6572b444 -->

## 2026-09-21 — Classify generated init files by byte content before writing

**Actor:** codex
**Role:** implementer
**Task:** RELAY-22

**Because:** Byte comparison leaves identical files untouched, preserves any differing user content without requiring it to decode as UTF-8, and lets overwrite deliberately rewrite every generated file

**Rejected:**

- Compare decoded text — invalid UTF-8 user edits would make init fail instead of preserving the file

**Files:** src/whyline_relay/init.py

<!-- whyline-event: e1794adb72e74283b356209d35b33b69 -->

## 2026-09-21 — RELAY-22 review round 1: approve non-destructive init with --overwrite

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-22

**Because:** init.run classifies each generated file: missing or --overwrite writes it, identical bytes are left untouched and silent, differing files are kept and reported with the exact 'Kept <relative path>: it already exists and differs. Run with --overwrite to replace it.' line; exit stays 0, the confirmation prompt, its default, --yes and the preamble and closing text are unchanged, README.md is untouched, and the --overwrite help text matches the spec verbatim. Replacing the old single 'Wrote X and Y.' line with one relative-path line per written or kept file is what the task's 'summary lists which files were written and which were kept' asks for. Tests cover fresh write, a no-edit second run (mtimes preserved, no Kept or Wrote), edited config/prompt/permissions kept, --overwrite through cli.main restoring everything, a missing file recreated among existing ones, and the help text; the existing decline and no-terminal tests still cover writing nothing. I ran uv run pytest -q myself: all 196 passed

**Rejected:**

- Request handling for a directory sitting where a generated file belongs — path.read_bytes would raise, but that is a pathological setup outside the task and the previous code failed there too

**Files:** src/whyline_relay/init.py

<!-- whyline-event: 6b5a83b6bdba473fa0eab8a779be3dec -->

## 2026-09-21 — Keep plan guidance as importable constants and validate its worked example with the production parser

**Actor:** codex
**Role:** implementer
**Task:** RELAY-23

**Because:** the CLI can print guidance without repository state while tests directly prevent the documented task format from drifting from plan.parse

**Rejected:**

- Duplicate the guide in the CLI and tests — multiple copies could diverge and would make exact prompt output harder to verify

**Files:** src/whyline_relay/planhelp.py, src/whyline_relay/cli.py, tests/test_planhelp.py

<!-- whyline-event: 5ea8df0f83b64033b615b039999acd82 -->

## 2026-09-21 — RELAY-23 review round 1: approve plan-format and planhelp constants as written

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-23

**Because:** output matches the spec exactly (RULES, blank line, heading, PROMPT; --prompt prints only PROMPT); every rule in the text agrees with plan.parse (id before first colon or first word, unique ids, indented detail, [x] skipped); the PROMPT example is parsed with plan.parse in a test; PROMPT is 162 words (<200); the command needs no repo, verified by a subprocess test in a tmp dir; suite passes with plain uv run pytest -q

**Rejected:**

- Request wording changes to the guidance — the text is clear and accurate, and the reviewer-flagged risk was wording only, so no change is warranted

**Files:** src/whyline_relay/planhelp.py, src/whyline_relay/cli.py, tests/test_planhelp.py

<!-- whyline-event: 4a258d5d24d448b6acd1e147085cd69d -->

## 2026-09-21 — Use one ordered preflight result model for doctor and launch gating

**Actor:** codex
**Role:** implementer
**Task:** RELAY-24

**Because:** Shared Check results keep doctor, start, and resume semantics identical while an injectable runner makes whyline and login probes hermetic in tests; resume validates its saved plan before any mutation

**Rejected:**

- Duplicate checks in each CLI command — output, ordering, and blocking behavior could drift
- Remove the existing launch guards — --skip-checks still needs the established live, branch, and dirty safety behavior

**Files:** src/whyline_relay/preflight.py, src/whyline_relay/cli.py, tests/test_preflight.py

<!-- whyline-event: c254b82edb2441f6abce7bdad4df2737 -->

## 2026-09-21 — RELAY-24 review round 1: approve preflight module, doctor command and start/resume gating

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-24

**Because:** diff matches the task: eight checks in the specified order with the exact ok/warn/FAIL line format, fix hints and summary line; doctor exits 1 only on FAIL; start/resume print only warn/FAIL to stderr and return before any state change, branch switch or agent launch; --skip-checks bypasses, --dry-run never reaches the preflight; runner is injectable with a real default and every preflight.run test passes a fake, so no real codex/claude/whyline runs in tests. Plain uv run pytest -q passes (all green), and a real doctor run on this repo printed the specified lines, including FAIL on the live relay marker. Known nits accepted: a missing codex/claude program gives both a FAIL (PATH) and a login warn; resume now also blocks on a dirty tree unless --allow-dirty, which follows the task's 'same checks' wording

**Rejected:**

- Request changes for the duplicate PATH-fail plus login-warn — the warn never blocks and the task defines the login check as warn when the status command cannot run
- Require an end-to-end test of doctor --plan/--allow-dirty passthrough — the flags are thin wiring and the module tests cover both behaviours directly

**Files:** src/whyline_relay/preflight.py, src/whyline_relay/cli.py, tests/test_preflight.py

<!-- whyline-event: f9ba945e9122464892539df4e9362e35 -->

## 2026-09-21 — Scope the displayed command name to each cli.main invocation

**Actor:** codex
**Role:** implementer
**Task:** RELAY-25

**Because:** A ContextVar-backed helper lets every command module render the caller's prog value while restoring the default after embedded calls, including parser exits

**Rejected:**

- Pass prog through every command function — it would spread an embedding concern across the CLI and helper modules
- Use a persistent module global — one embedded call could leak its name into later default calls

**Files:** src/whyline_relay/invocation.py, src/whyline_relay/cli.py

<!-- whyline-event: 5503d5d8f2cc4120bbaf490afb00fa2b -->

## 2026-09-21 — Approve RELAY-25: prog-scoped guidance via ContextVar; accept argparse SystemExit and process-wide SIGINT handler as known embedding limits

**Actor:** claude
**Role:** reviewer
**Task:** RELAY-25

**Because:** All hard-coded whyline-relay <command> hints now go through invocation.command(); the guard test, prog=whyline relay tests and default-behaviour tests pass (237). main itself never calls sys.exit and ignores sys.argv when argv is given. argparse still raises SystemExit for --help/--version/usage errors and main installs a SIGINT handler, both pre-existing behaviours the task did not ask to change

**Rejected:**

- Catch SystemExit in main and return its code — the task said keep default behaviour exactly as today, and existing version/help tests rely on SystemExit
- Request changes for the planhelp.rules() string-replace on a default-prog constant — slightly fragile, but pinned by tests and existing RULES/PROMPT tests still hold

**Files:** src/whyline_relay/invocation.py, src/whyline_relay/cli.py, tests/test_embedding.py

<!-- whyline-event: 02644da106b64f26a9c8fac8830c415a -->

## 2026-09-22 — Map configured generic agent names to the generic adapter while deriving built-in defaults from the registry

**Actor:** codex
**Role:** implementer
**Task:** ADPT-1

**Because:** agent commands remain keyed by role-visible names, while adapter_for can select capability behavior without changing the codex and claude 0.2.1 defaults

**Rejected:**

- Use agent names directly as adapter names — configured generic names such as aider would fail registry lookup

**Files:** src/whyline_relay/config.py, src/whyline_relay/adapters/__init__.py

<!-- whyline-event: e41e0797c60a4f87a130a510eb0b047d -->

## 2026-09-22 — Approve ADPT-1: adapter registry matches spec exactly, moved allowlist code unchanged, config.load role/adapter validation messages match verbatim

**Actor:** claude
**Role:** reviewer
**Task:** ADPT-1

**Because:** diff reviewed line-by-line against spec; all 15 new tests plus 237 existing pass (252 total); 0.2.1 default commands, BASE_ALLOW/DENY/PRESETS, and loop._no_handoff_detail logic preserved unchanged

**Files:** src/whyline_relay/config.py, src/whyline_relay/adapters/claude.py

<!-- whyline-event: 4acbca328c1741768acfb5d14b1af4e9 -->

## 2026-09-22 — Approve ADPT-2: routing.decide takes implementer/reviewer params, Handoff gains from_actor

**Actor:** claude
**Role:** reviewer
**Task:** ADPT-2

**Because:** diff is scoped exactly to the four named files, matches the spec's signature and field additions verbatim, default IMPLEMENTER/REVIEWER preserve relay 0.2.1 behavior for existing callers in loop.py, and the new tests match the spec's required cases; full suite (258 tests) passes with the plain uv run pytest -q command

**Files:** src/whyline_relay/routing.py, src/whyline_relay/handoff.py

<!-- whyline-event: 6e2059c000bb47629d03c961f56cf045 -->

## 2026-09-22 — Render role placeholders before inserting user content

**Actor:** codex
**Role:** implementer
**Task:** ADPT-3

**Because:** Replacing implementer and reviewer on the template first preserves literal role placeholder text supplied in task content while keeping default prompt bytes unchanged

**Rejected:**

- Replace all placeholders in one pass after content insertion — role-looking text in task content could be rewritten

**Files:** src/whyline_relay/prompts.py

<!-- whyline-event: 90391dbfafe04c4fbf941a38643b5491 -->

## 2026-09-22 — ADPT-3 review: approve

**Actor:** claude
**Role:** reviewer
**Task:** ADPT-3

**Because:** IMPLEMENT and REVIEW templates now use {implementer}/{reviewer} placeholders substituted before user content is inserted, preventing task text collisions; golden files captured from unmodified 0.2.1 code prove default rendering is byte-for-byte unchanged; new tests cover default bytes, custom-role substitution, and literal-placeholder-in-task-text safety; only the two permitted lines in test_prompts.py were edited; init.py untouched; full suite (261 tests) passes with plain uv run pytest -q

**Files:** src/whyline_relay/prompts.py

<!-- whyline-event: 2c269f04fb624e9a80e7cf6c3d4d6a4f -->
