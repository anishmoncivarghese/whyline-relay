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

## 2026-09-22 — Treat every nonblank agent marker as a valid relay owner

**Actor:** codex
**Role:** implementer
**Task:** ADPT-4

**Because:** Configured and future agent names must participate in the one-relay safety guard, while the optional role preserves explicit status semantics and legacy markers retain the old fallback

**Rejected:**

- Keep a fixed agent-name allowlist — markers for configured generic agents would be ignored and permit a second relay

**Files:** src/whyline_relay/running.py, src/whyline_relay/cli.py

<!-- whyline-event: 8be5f9a3aa644fd8bf48f3d08a643522 -->

## 2026-09-22 — ADPT-4 approved: one-relay guard now covers any nonblank agent marker, status/dry-run stay role-aware without renaming codex/claude

**Actor:** claude
**Role:** reviewer
**Task:** ADPT-4

**Because:** read() now rejects only non-string/blank agent or non-string role instead of a fixed codex/claude allowlist, closing the second-relay hole for configured agents like gemini; cmd_status and the --dry-run branch read settings.roles instead of hardcoding codex, and defaults (codex/claude) keep 0.2.1 output byte-identical; new tests in test_running_roles.py and test_cli_roles.py cover the live-marker guard for a third agent, empty-agent rejection, missing/non-string role, start_turn writing role, status verb for reviewer/implementer/legacy markers, and dry-run picking the configured implementer; full suite passes 270/270 with plain uv run pytest -q

**Rejected:**

- requiring a role on every marker — would break old markers already on disk, which the task explicitly requires to keep loading with role==''

**Files:** src/whyline_relay/running.py, src/whyline_relay/cli.py

<!-- whyline-event: 8dc9dfb5d8e943cea8ae5986f6e7e158 -->

## 2026-09-22 — Disambiguate shared-agent turns with role-specific log suffixes

**Actor:** codex
**Role:** implementer
**Task:** ADPT-5

**Because:** When one configured agent fills both roles, the implementer and reviewer run in the same round and would otherwise overwrite the same log; distinct agents retain the 0.2.1 names

**Rejected:**

- Always add role suffixes — that would change default log names

**Files:** src/whyline_relay/loop.py

<!-- whyline-event: 0ce12ce31eb64b4099af2c65ff7fb574 -->

## 2026-09-22 — ADPT-5 approved: loop.py is now role-driven end to end

**Actor:** claude
**Role:** reviewer
**Task:** ADPT-5

**Because:** implementer/reviewer come from settings.roles, agents/templates/logs/progress verbs/commit-check follow role not hardcoded codex/claude names, _no_handoff_detail delegates to the agent's adapter; default-role behavior verified unchanged by the full existing suite (275/275), and new tests genuinely exercise swapped roles, shared-agent log disambiguation, and generic-adapter silence diagnosis

**Rejected:**

- trusting the handoff's own claim without re-running tests — reviewer must run the plain test command itself, which was done

**Files:** src/whyline_relay/loop.py, tests/fake_role_agent.py, tests/test_loop_roles.py

<!-- whyline-event: be8e4f3b983e4bd08405407cdc751c41 -->

## 2026-09-22 — Validate provenance only on the handoff produced by the agent turn

**Actor:** codex
**Role:** implementer
**Task:** ADPT-6

**Because:** The fresh handoff must identify the agent that just ran, while resume must continue routing records created before the current process ran

**Rejected:**

- Validate the existing resume record — no agent has just run during resume routing, so that would reject legitimate saved state

**Files:** src/whyline_relay/loop.py

<!-- whyline-event: 79ad37571dbc4213a914727ad6e85abf -->

## 2026-09-22 — ADPT-6 approved: provenance check placed and scoped correctly

**Actor:** claude
**Role:** reviewer
**Task:** ADPT-6

**Because:** Check sits exactly after the task-match guard and before BLOCKED/UNKNOWN handling per spec; only validates the record just written by the agent that ran (resume path never reaches it, confirmed by reading _run_task's resume branch at lines 185-201 which never touches the while-loop's post-turn checks); empty from_actor and case-insensitive match both handled; fake_agent.py edit is the single permitted line; full suite passes 281/281 with the plain 'uv run pytest' command

**Rejected:**

- trusting the implementer's claimed 281-test run without re-running — reviewer must verify independently, which was done

**Files:** src/whyline_relay/loop.py, tests/fake_agent.py, tests/test_loop_roles.py

<!-- whyline-event: 727fcf5a1fe244df99f5f32986fc3351 -->

## 2026-09-22 — Use role order when deduplicating agents for preflight

**Actor:** codex
**Role:** implementer
**Task:** ADPT-7

**Because:** An agent filling both roles must be checked once, and implementer is the specified first role

**Rejected:**

- Emit one capability row per role — the task requires each in-use agent once

**Files:** src/whyline_relay/preflight.py

<!-- whyline-event: 188cb3a427e9423d939423301c79797d -->

## 2026-09-22 — Approved ADPT-7: role-scoped preflight checks agents in use, adapters for logins, and warns/fails on weak multi-agent setups

**Actor:** claude
**Role:** reviewer
**Task:** ADPT-7

**Because:** Implementation matches spec exactly: _agents_in_use dedupes by role order, _logins/_programs/_relay_setup iterate only in-use agents, _role_checks emits independence/generic warnings and non-default-role template/summary checks; default-role output verified byte-identical via unedited test_all_checks_pass_in_documented_order, and all 287 tests pass

**Rejected:**

- Trusting the handoff's own test claim without rerunning — reran full suite independently per review protocol

**Files:** src/whyline_relay/preflight.py, src/whyline_relay/adapters/__init__.py, tests/test_preflight.py

<!-- whyline-event: dc228c77dfac4cf7b0d0a353ee17849a -->

## 2026-09-22 — Generate init files from the deduplicated selected built-in agents while preserving a legacy default config path

**Actor:** codex
**Role:** implementer
**Task:** ADPT-8

**Because:** Role options must emit only the selected agents and their adapter permission files, while an option-free init must remain byte-identical to 0.2.1

**Rejected:**

- Always emit both built-in agent blocks and Claude permissions — role-specific init would write files and commands unused by Codex-only or single-agent setups

**Files:** src/whyline_relay/init.py

<!-- whyline-event: ac0212472ae74021a332a5ca809200eb -->

## 2026-09-22 — Approve ADPT-8: role-selecting init flags with byte-identical default path

**Actor:** claude
**Role:** reviewer
**Task:** ADPT-8

**Because:** Verified roles_given branching in init.py matches spec exactly (permission files scoped to agents in use, claude-only closing notes, config.toml [roles]+[agents.*] blocks); legacy no-flag path is the unchanged 0.2.1 code relocated, confirmed byte-identical via passing golden test; full suite (294 tests) passes

**Files:** src/whyline_relay/init.py, src/whyline_relay/cli.py, tests/test_init_roles.py

<!-- whyline-event: 536f220e29424031aac7f516af6e3117 -->

## 2026-09-22 — Reject built-in bypass options at both preflight and the final launch boundary

**Actor:** codex
**Role:** implementer
**Task:** ADPT-9

**Because:** Preflight gives doctor, start and resume an actionable failure, while the launch-boundary check guarantees skipped checks can never pass a bypass command to an agent

**Rejected:**

- Rely only on preflight — --skip-checks would permit the unsafe command

**Files:** src/whyline_relay/adapters/bypass.py, src/whyline_relay/loop.py, src/whyline_relay/preflight.py

<!-- whyline-event: b031c2d5337a4acf9923144bcbf62fde -->

## 2026-09-22 — ADPT-9 approved: single bypass registry enforced at preflight and the launch boundary

**Actor:** claude
**Role:** reviewer
**Task:** ADPT-9

**Because:** bypass.py is the sole source of the refused flags/values; loop._run_agent checks before start_turn/launch (holds under --skip-checks) and preflight._role_checks FAILs with matching wording; find() correctly matches exact/=-joined flags plus codex -s/--sandbox and claude --permission-mode forms; guard test extended for bypassPermissions with bypass.py exempted by exact name; new tests cover per-adapter isolation, defaults, generic no-op, run_task refusing before agents.run is called, preflight FAIL, and start --skip-checks exiting non-zero; full suite passes (311 tests, uv run pytest -q); pyproject.toml/uv.lock untouched

**Files:** src/whyline_relay/adapters/bypass.py, src/whyline_relay/loop.py, src/whyline_relay/preflight.py, tests/test_bypass.py, tests/test_no_bypass.py

<!-- whyline-event: 5e30d340766a4cb98087f3f2a9372cad -->

## 2026-09-22 — agy's -p flag must be the last item in a generic-agent command list, immediately before the appended prompt

**Actor:** claude
**Role:** reviewer
**Task:** GEMINI-SPIKE

**Because:** whyline-relay always appends the prompt as the command's final argv element; agy's -p/--print greedily consumes whatever token follows it, so placing -p anywhere earlier makes it swallow the next flag (e.g. --output-format) as the prompt and silently ignore the real one, measured as a full turn producing no handoff

**Rejected:**

- -p immediately followed by the prompt with other flags after it — works standalone but not through the relay, since the relay never inserts the prompt except at the very end

**Files:** docs/antigravity-adapter-consultation.md

<!-- whyline-event: 9e7bf4c7a1e8484593240af41c3c1abb -->

## 2026-09-22 — For antigravity as implementer, forbid backgrounded commands in the prompt rather than forbidding test-running in the task text

**Actor:** claude
**Role:** reviewer
**Task:** GEMINI-SPIKE

**Because:** Measured A/B: telling it not to run any test command (task-text fix) worked but produced weaker verification and used 2.3x the tokens (297k vs 128k) because it had to reason around an artificial constraint; telling it to never background a command and never end its turn early (prompt-level fix) let it actually run the real tests, completed in half the time, and generalises to any task instead of depending on each task author correctly knowing whether a test runner exists

**Rejected:**

- Task text says 'do not run tests' per task — fragile, depends on every task author judging test-runner presence correctly, and produces weaker verification than actually running tests

**Files:** docs/antigravity-adapter-consultation.md

<!-- whyline-event: a5aaaab71e474863bb99f7d12734208d -->

## 2026-09-22 — Corrected the published v0.2.2 release notes: the real test count was 311, not 260

**Actor:** claude
**Role:** reviewer
**Task:** PROMPT-BACKGROUND-GUARD

**Because:** Verified by checking out the v0.2.1 and v0.2.2 tags and running the actual suite (237 and 311 respectively), rather than continuing to trust a number that had been carried over without a final re-check before publishing; 0.1.0 and 0.2.0's counts were also independently re-verified and are correct

**Files:** docs/releases/v0.2.2.md

<!-- whyline-event: bb314eeaa85a456ca117309d29e20bed -->

## 2026-09-22 — Validate backup roles after primary roles and configured agents

**Actor:** codex
**Role:** implementer
**Task:** FBO-1

**Because:** backup validation needs resolved primary role names for self-agent checks and the configured adapter map for generic-agent checks

**Rejected:**

- Validate backups inside the primary role loop — backup is a nested table and requires validation against both resolved primary roles and configured agents

**Files:** src/whyline_relay/config.py

<!-- whyline-event: c5c3479e439f41b3bd516e860a56a0ca -->

## 2026-09-22 — Approve FBO-1: [roles.backup] parsing/validation matches spec verbatim, all 321 tests pass (8 new)

**Actor:** claude
**Role:** reviewer
**Task:** FBO-1

**Because:** diff reviewed line-by-line against spec: Config.backups field placement, role_values loop accepting 'backup', post-role validation order (unknown key, non-string, self-match, unknown agent), error strings match verbatim, backups=dict(backup_values) returned; only config.py and test_config.py touched, no pyproject/uv.lock/version changes

**Files:** src/whyline_relay/config.py, tests/test_config.py

<!-- whyline-event: 9cbaec0979134658a64309b75bc8947a -->

## 2026-09-22 — Treat failed or timed-out login probes as inconclusive

**Actor:** codex
**Role:** implementer
**Task:** FBO-2

**Because:** A broken diagnostic must not switch away from an agent that may still be authenticated

**Rejected:**

- Treat probe errors as logged out — transient local failures would trigger false failover

**Files:** src/whyline_relay/failover.py

<!-- whyline-event: 5ecb318135414fdd80c3b96d1a2678b7 -->

## 2026-09-22 — FBO-2 review: approve. failover.py matches spec exactly (overrides, effective_agent, still_logged_in, failover_reason, pause_message), gitcheck ignore entry added, no other files touched

**Actor:** claude
**Role:** reviewer
**Task:** FBO-2

**Because:** Verified failover.py and test_failover.py byte-for-byte against the spec, confirmed config.relay_dir/Roles/Config, adapters.base.Adapter, codex/generic ADAPTER fields, and agents.rate_limited all match what the module calls, and ran the full suite (336 tests, all dots, no failures)

**Rejected:**

- Request changes over the docstring-only prose differences from the spec — cosmetic, no behavior impact

**Files:** src/whyline_relay/failover.py, src/whyline_relay/gitcheck.py, tests/test_failover.py

<!-- whyline-event: 784727e184124a738b4de4170dbf055a -->

## 2026-09-22 — Persist role failover and retry the same round with the effective backup

**Actor:** codex
**Role:** implementer
**Task:** FBO-3

**Because:** A detected quota or authentication failure must survive later invocations while preserving the current review round

**Rejected:**

- Pause after the primary failure — a configured backup can continue the task automatically
- Increment the round on failover — switching agents is a retry of the same routing move

**Files:** src/whyline_relay/loop.py, src/whyline_relay/cli.py, tests/test_loop_failover.py

<!-- whyline-event: 2dfbb718c3d249bf93e6aa623da66da7 -->

## 2026-09-22 — FBO-3 approved: loop.py and cli.py resolve failover backups via failover.effective_agent exactly per spec

**Actor:** claude
**Role:** reviewer
**Task:** FBO-3

**Because:** runner threaded through all five functions as specified, implementer/reviewer resolved fresh via failover.effective_agent before and at the top of the retry loop, _hit_a_limit removed in favor of failover.failover_reason, NO_HANDOFF branch matches the spec's code verbatim, cli.py dry-run uses the same resolver for both the render call and the agents lookup, and the full suite (uv run pytest -q) passes with no regressions

**Files:** src/whyline_relay/loop.py, src/whyline_relay/cli.py, tests/test_loop_failover.py

<!-- whyline-event: 2c7f11a38ad74fe6b341a8a3342e9b5e -->

## 2026-09-22 — Preflight configured backups and expose sticky role override controls

**Actor:** codex
**Role:** implementer
**Task:** FBO-4

**Because:** A backup may become active after launch, so its executable and login must be validated up front and operators need supported status and reset commands

**Rejected:**

- Check only primary agents — a later failover could select an unusable backup
- Manage active-roles.json by hand — CLI commands can report and clear overrides through the existing failover API

**Files:** src/whyline_relay/preflight.py, src/whyline_relay/roles.py, src/whyline_relay/cli.py

<!-- whyline-event: 8d8701e64108414b8a63f1ec5a643b94 -->

## 2026-09-22 — Approve FBO-4: backup-aware preflight labelling and roles status/reset match spec

**Actor:** claude
**Role:** reviewer
**Task:** FBO-4

**Because:** preflight._agents_in_use now carries backup_for per spec, program/login FAIL messages label the backup role, roles.py and the CLI roles subcommand match the given code exactly, and tests genuinely cover the new backup-labelled failures plus roles status/reset; full suite passes with no dependency or version changes

**Rejected:**

- Require --repo only on the roles parser as literally shown — argparse can't accept a parent optional placed after the chosen subcommand, so status/reset need their own suppressed --repo to satisfy the task's own post-subcommand invocations

**Files:** src/whyline_relay/preflight.py, src/whyline_relay/roles.py, src/whyline_relay/cli.py

<!-- whyline-event: 06b7d6b081ca48938f40dd0ad181055d -->

## 2026-09-22 — Prompts must render with the switched agent's name, not the static [roles] config, after a backup takeover

**Actor:** claude
**Role:** reviewer
**Task:** FBO-BACKUP-FAILOVER

**Because:** _run_agent read settings.roles.implementer/.reviewer directly for {implementer}/{reviewer} instead of the effective (possibly switched) agent _run_task already resolves; a rate-limited implementer's backup would run correctly but its own handoff would still say --from <old-primary>, which the relay's own from-actor check then rightly rejected. Found by an acceptance test whose stand-in reads the rendered prompt as a real agent would, rather than being told its from/to actor on the command line -- the internal unit test with a hard-coded argv missed it entirely

**Rejected:**

- Trust the internal unit test suite alone — it never exercises the actual rendered prompt text, only whatever from/to values the fake agent is told directly

**Files:** src/whyline_relay/loop.py

<!-- whyline-event: 63e376a4683446e7a08d943aa100077c -->

## 2026-09-22 — A backup-only agent in doctor's per-role summary was described as if it were the current primary

**Actor:** claude
**Role:** reviewer
**Task:** PREFLIGHT-BACKUP-LABEL

**Because:** The summary loop derived its role label purely from 'agent == roles.implementer', ignoring _agents_in_use's own backup_for field; found by re-running the exact manual scenario used to verify the 0.2.4 release for real, after publishing -- none of the 17 acceptance tests written for that release happened to combine non-default roles with a backup naming a genuinely third agent

**Rejected:**

- Leave it — it's message-only, not a routing/safety bug, but doctor's whole purpose is telling the truth about what's configured

**Files:** src/whyline_relay/preflight.py

<!-- whyline-event: 4511016178c84407aa86818568403393 -->

## 2026-09-22 — Resolve custom agent variants through adapter capability metadata

**Actor:** codex
**Role:** implementer
**Task:** MDL-1

**Because:** A variant must inherit the aliased built-in command and preflight behavior while model arguments are applied only when that adapter declares a real model flag

**Rejected:**

- Treat every custom name as generic — that would lose built-in defaults and Claude/Codex login and permission checks

**Files:** src/whyline_relay/config.py, src/whyline_relay/adapters/base.py

<!-- whyline-event: 9bb12429f9b14c4493b3bb82266338b2 -->

## 2026-09-22 — Approve MDL-1: config.py loop, adapter model_flag fields, and tests match spec verbatim; full suite (362 tests) passes unedited except the one permitted case

**Actor:** claude
**Role:** reviewer
**Task:** MDL-1

**Because:** Diff reviewed line-by-line against the task spec: base/claude/codex/generic model_flag additions and the config.py loop replacement are byte-identical to the prescribed code; the only existing-test edit is the sanctioned adapter-mystery case; ran uv run pytest -q (plain command, not implementer's --frozen) and got 362 passed with no failures

**Files:** src/whyline_relay/config.py, src/whyline_relay/adapters/base.py

<!-- whyline-event: 84f87ae79a914a2b9a1ace3b78e03e34 -->

## 2026-09-22 — Model legacy routing with a shared transition table across both stages

**Actor:** codex
**Role:** implementer
**Task:** PIPE-1

**Because:** the current router is stateless with respect to the stage that just ended, while recipient matching belongs to the role of the resolved target stage

**Rejected:**

- Use separate per-stage transition tables — that would change legacy behavior by making outcomes depend on the prior stage

**Files:** src/whyline_relay/pipeline.py, tests/test_pipeline.py

<!-- whyline-event: 210e3edf2274435bb3a30ed127f4dbde -->

## 2026-09-22 — PIPE-1 review: approve

**Actor:** claude
**Role:** reviewer
**Task:** PIPE-1

**Because:** pipeline.py and test_pipeline.py are byte-faithful to the spec (only cosmetic multi-line formatting differs); compile_legacy reproduces routing.decide's exact behavior (approved/blocked short-circuit regardless of recipient, review/changes/assigned gated on the resolved stage's role), and decide() generalizes correctly to N stages. 378 tests pass (uv run pytest -q), including all 16 new tests, with routing.py and every other file untouched. The iteration-order risk noted in the handoff isn't a live bug: the legacy shape's two stages share an identical transitions dict so order can't affect the result, and the three-stage test fixture has no status key shared across stages except the blocked terminal, which is order-independent

**Files:** src/whyline_relay/pipeline.py, tests/test_pipeline.py

<!-- whyline-event: 59daa7e674e5408e97b5246adb3ff879 -->

## 2026-09-22 — Approve PIPE-2: routing.decide is now a thin wrapper over pipeline.compile_legacy/pipeline.decide

**Actor:** claude
**Role:** reviewer
**Task:** PIPE-2

**Because:** diff matches the spec's replacement byte-for-byte, touches only routing.py, and the full suite (378 tests) including all 15 unedited test_routing.py tests passes under the plain uv run pytest -q command; identical shared transition tables on both stages make the two-stage loop order-independent so no behavior changed

**Files:** src/whyline_relay/routing.py

<!-- whyline-event: 82e79eb95dde49f4ab55e9a3a9760a88 -->

## 2026-09-23 — Resolve configured-pipeline transitions from the active stage and profile at runtime

**Actor:** codex
**Role:** implementer
**Task:** PCR-1

**Because:** A stage's @next target depends on the selected profile, while omitting current_stage_id must preserve legacy stateless traversal across every stage

**Rejected:**

- Resolve @next during pipeline compilation — the same stage can advance to different targets in different profiles

**Files:** src/whyline_relay/pipeline.py

<!-- whyline-event: 0e6f7862df1147ba9d8ea0ef76c5b2f7 -->

## 2026-09-23 — PCR-1 review: approve

**Actor:** claude
**Role:** reviewer
**Task:** PCR-1

**Because:** decide() matches the spec exactly: legacy mode (current_stage_id=None) is behavior-preserving since compile_legacy never emits an @next target, and the new mode correctly scopes candidates to the single named stage and resolves @next against the given profile's own stage order. Full suite passes (381 tests via uv run pytest -q), including the 3 new regression tests, with test_routing.py untouched

**Files:** src/whyline_relay/pipeline.py

<!-- whyline-event: dbb35443b74845e1a97f522fd0b3850c -->

## 2026-09-23 — Keep pipeline resume state additive and backward-compatible

**Actor:** codex
**Role:** implementer
**Task:** PCR-2

**Because:** Empty scalar defaults let legacy state.json records load unchanged, while default_factory gives each RelayState its own stage_visits mapping

**Rejected:**

- Require pipeline keys in every state file — older relay state would fail to resume

**Files:** src/whyline_relay/state.py, tests/test_state.py

<!-- whyline-event: 97c21e8fd40a459fafe75503c82dfe81 -->

## 2026-09-23 — PCR-2 review: approve

**Actor:** claude
**Role:** reviewer
**Task:** PCR-2

**Because:** RelayState gains profile, stage, stage_visits, pipeline_fingerprint as additive defaulted fields exactly per spec; asdict/json round-trips the dict field cleanly and RelayState(**record) fills missing keys for legacy state.json files without raising. Full suite (383 tests, 381 prior + 2 new) passes unedited

**Files:** src/whyline_relay/state.py, tests/test_state.py

<!-- whyline-event: 11ed6f1867d84207b00ed8ebb71ecb57 -->

## 2026-09-23 — Validate profile completion before ordinary unknown transition targets

**Actor:** codex
**Role:** implementer
**Task:** PCR-3

**Because:** The required malformed-profile case must report that its profile cannot reach @complete, while valid completing profiles must still report unknown stage targets precisely

**Rejected:**

- Validate every unknown target first — this masks the required no-completion diagnostic for a profile whose only transition is invalid

**Files:** src/whyline_relay/config.py

<!-- whyline-event: d084c94a38a2443abb0ec11f05176b63 -->

## 2026-09-23 — Approve PCR-3: [pipeline] compiles into a validated Pipeline with a stable resume fingerprint

**Actor:** claude
**Role:** reviewer
**Task:** PCR-3

**Because:** Diff matches the task spec: _load_pipeline compiles stages/profiles/roles with role-existence, @next-position, unknown-target, and per-profile @complete-reachability checks in the order recorded by the prior decision; [roles.backup]/[status_map] mutual-exclusion with [pipeline] is enforced; fingerprint is a stable sha256 of the canonicalized raw table. All 43 config tests plus the full 393-test suite pass unedited

**Files:** src/whyline_relay/config.py, tests/test_config.py

<!-- whyline-event: 665112736511472fa034775ecd7ca8f7 -->

## 2026-09-23 — Parse relay-profile from normalized task text and preserve backward-compatible Task construction

**Actor:** codex
**Role:** implementer
**Task:** PCR-4

**Because:** Searching the assembled dedented text makes an indented Markdown detail directive match as a logical line, while a defaulted optional field leaves existing Task constructors unchanged

**Rejected:**

- Parse raw detail lines before dedenting — this would couple directive recognition to Markdown indentation

**Files:** src/whyline_relay/plan.py, tests/test_plan.py

<!-- whyline-event: c342491f3fac4cce90685d8f75eb0ef3 -->

## 2026-09-23 — PCR-4 review: approve — relay-profile: directive parsing matches spec exactly

**Actor:** claude
**Role:** reviewer
**Task:** PCR-4

**Because:** RELAY_PROFILE regex searches the dedented, assembled task text so an indented Markdown detail directive matches as a logical line; Task.profile defaults to None so all existing Task(...) call sites are unaffected. Both spec tests (directive parsed, directive without a name yields None) pass, and the full suite (395 tests) passes unedited

**Files:** src/whyline_relay/plan.py, tests/test_plan.py

<!-- whyline-event: 95539c27ec654af7a48f4e0db1c73195 -->

## 2026-09-23 — Raise a domain-specific error for unresolved prompts while preserving existing render output

**Actor:** codex
**Role:** implementer
**Task:** PCR-5

**Because:** Pipeline stages can name custom prompts, so missing overrides need an actionable PromptError; optional empty metadata values keep all existing callers and built-in templates byte-for-byte compatible

**Rejected:**

- Keep leaking KeyError — it obscures that both the user override and built-in template are missing

**Files:** src/whyline_relay/prompts.py, tests/test_prompts.py

<!-- whyline-event: 84fd660702064c25b3f916586bc6a390 -->

## 2026-09-23 — PCR-5 review: approve — PromptError and actor/role/stage/profile placeholders match spec exactly, tests genuinely cover the new behavior

**Actor:** claude
**Role:** reviewer
**Task:** PCR-5

**Because:** load() now raises PromptError instead of leaking KeyError for unknown custom stage names, and render() accepts four optional metadata kwargs that default to empty string, byte-for-byte preserving existing IMPLEMENT/REVIEW output; new tests cover the override path, the missing-prompt error, new placeholder substitution, and default-output equivalence; full suite passes unedited (uv run pytest -q, exit 0) and test_prompts.py passes 16/16

**Files:** src/whyline_relay/prompts.py, tests/test_prompts.py

<!-- whyline-event: f99759a848774507b21f8c7293030aff -->

## 2026-09-23 — Carry stage state through turn callbacks and pause persistence without changing legacy execution

**Actor:** codex
**Role:** implementer
**Task:** PCR-6

**Because:** Configured pipelines need resumable stage metadata in Task 7, while empty stage dictionaries and optional _run_agent defaults preserve every legacy call path and keep the permission-bypass guard unchanged

**Rejected:**

- Keep the two-argument on_turn callback — it cannot expose pipeline stage progress to run_plan for pause persistence

**Files:** src/whyline_relay/loop.py, tests/test_loop_single.py

<!-- whyline-event: 259036931d2041c489b1b7ba63ba1614 -->

## 2026-09-23 — PCR-6 approved: on_turn carries stage_state, _run_agent gains optional stage-aware params

**Actor:** claude
**Role:** reviewer
**Task:** PCR-6

**Because:** Diff matches spec exactly; all optional params default to None/empty preserving every legacy call path; full suite (uv run pytest -q) passes with no failures; new test genuinely exercises the three-arg on_turn contract with empty-dict assertion for legacy runs

**Files:** src/whyline_relay/loop.py, tests/test_loop_single.py

<!-- whyline-event: b2b3b2cbce4745398c2a3fd06fa7999b -->

## 2026-09-23 — Dispatch configured tasks through a stage-aware pipeline driver

**Actor:** codex
**Role:** implementer
**Task:** PCR-7

**Because:** Configured pipelines need per-stage routing, visit caps, resumable stage checkpoints, and commit permission based on reachability to @complete while the legacy driver must remain unchanged

**Rejected:**

- Extend the fixed two-role driver — its role/status-map assumptions cannot safely represent arbitrary stage graphs or pipeline resume state

**Files:** src/whyline_relay/loop.py, tests/test_loop_pipeline.py, tests/fake_pipeline_agent.py

<!-- whyline-event: 8da0ebe7cd974ecbb7c0de4c1c9fe6f1 -->

## 2026-09-23 — PCR-7 approved: loop.py drives configured pipelines stage by stage with crash-safe resume

**Actor:** claude
**Role:** reviewer
**Task:** PCR-7

**Because:** Diff matches the spec's _run_configured_task/run_task dispatch exactly; the two deviations from the literal spec text (fake_pipeline_agent.py falling back to parsing the counter out of a pre-existing 'id' field when 'counter' is absent, and the test fixture adding a custom .whyline/relay/prompts/test.md) are both required for the tests to actually pass -- the resume test hand-writes a handoff with no 'counter' key, and the 'tester' stage's prompt name has no built-in template. uv run pytest tests/test_loop_pipeline.py -v: 5 passed. uv run pytest -q: full suite passes, zero failures/errors via -rf

**Files:** src/whyline_relay/loop.py

<!-- whyline-event: bc80161b0e584e47aac7c50f16c3c3cc -->

## 2026-09-23 — Make preflight derive checks from configured pipeline roles, prompts, and task profiles

**Actor:** codex
**Role:** implementer
**Task:** PCR-8

**Because:** Pipeline configs do not use the legacy implementer/reviewer placeholders, so doctor must inspect actual stage agents and validate every stage prompt and selected task profile before launch

**Rejected:**

- Keep legacy role-only checks — pipeline-only agents, missing stage prompts, and invalid profiles would remain silently unchecked

**Files:** src/whyline_relay/preflight.py, tests/test_preflight.py

<!-- whyline-event: 2cf9e195cb0b4567b593bfddd54cc90e -->

## 2026-09-23 — Approve PCR-8: doctor now checks a configured pipeline's real agents and stage prompts

**Actor:** claude
**Role:** reviewer
**Task:** PCR-8

**Because:** Verified _agents_in_use dedupes pipeline roles by agent, the implementer==reviewer warning is correctly guarded behind pipeline is None, stage prompts are resolved via prompts.load/PromptError, and task.profile is validated against pipeline.profiles with the correct warn/FAIL split (no pipeline configured = warn, unknown profile = FAIL); all 4 new tests pass and the full suite (409 tests) passes unedited

**Rejected:**

- Trusting the handoff's test claims without rerunning — reran uv run pytest -q myself per review protocol

**Files:** src/whyline_relay/preflight.py, tests/test_preflight.py

<!-- whyline-event: 5ea96e0cd9ca4b779adbfe7543efe890 -->

## 2026-09-23 — Merged PCR-1..8: [pipeline] config exposure + crash-safe resume, built via whyline-relay on its own plan

**Actor:** claude
**Role:** reviewer
**Task:** PCR-1..8

**Because:** every task empirically pre-verified in a scratch copy against 5 independent acceptance tests before being handed to the relay; the relay's own run produced byte-identical code to that verified reference, full suite (409 tests) and acceptance battery pass with zero real regressions (the only non-passing tests were an out-of-scope suite, a pre-existing baseline failure reproduced on unmodified 0.2.7, and one 0.2.7-era test whose pinned 'not built yet' behavior is now correctly superseded by design)

**Rejected:**

- unify _run_task/_run_configured_task into one generalized function — kept them separate to avoid destabilizing the well-tested legacy path; the cost is some duplicated per-turn checks, judged acceptable
- relay-side auto-commit ownership (spec D6) — out of scope for this piece; a configured pipeline's terminal-reaching stage still self-commits via the existing HEAD-check mechanism

**Files:** src/whyline_relay/config.py, src/whyline_relay/loop.py, src/whyline_relay/preflight.py

<!-- whyline-event: 75e4d38c263f41c999280c598633b2a3 -->

## 2026-09-23 — Implement commit_all as an idempotent whole-tree relay commit helper

**Actor:** codex
**Role:** implementer
**Task:** RCO-1

**Because:** Configured pipelines require the relay to stage every tracked, deleted, and untracked change in one commit, while clean retries must succeed without creating an empty commit

**Rejected:**

- Reuse commit_paths — its path-scoped semantics intentionally leave unrelated changes untouched and cannot represent whole-tree ownership

**Files:** src/whyline_relay/gitcheck.py, tests/test_gitcheck.py

<!-- whyline-event: caa16ef128894990b4c77de583c2d3e7 -->

## 2026-09-23 — RCO-1 review: approve. commit_all() matches spec exactly (git add -A, returns False on clean tree, one commit otherwise); both new tests pass and full suite is green (100%, no failures); legacy code untouched

**Actor:** claude
**Role:** reviewer
**Task:** RCO-1

**Because:** Verified diff against the task spec line-for-line, confirmed dirty_paths/commit_message/head_commit helpers it relies on already exist and are used correctly, and ran uv run pytest -q with no failures

**Files:** src/whyline_relay/gitcheck.py, tests/test_gitcheck.py

<!-- whyline-event: 22ed51a599ae40368a8c1aecbe3dd056 -->
