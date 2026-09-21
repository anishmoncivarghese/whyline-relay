# whyline-relay

Give it a Markdown plan. It runs each task through **Codex** (implements) and **Claude** (reviews and commits), one task at a time, unattended, and stops when something needs a human.

```text
   plan.md                     ┌──────────────── one task ────────────────┐
 ┌───────────────┐             │                                          │
 │ - [ ] T-1 ... │──next task─▶│ Codex implements ─▶ hands off for review │
 │ - [ ] T-2 ... │             │        ▲                    │            │
 └───────────────┘             │   changes requested         ▼            │
        ▲                      │        └──────────── Claude reviews      │
        │                      │                      runs the tests      │
        │ ticks the box,       │                      commits, approves   │
        │ commits plan.md      └──────────────────────────┬───────────────┘
        └─────────────────────────────────────────────────┘
```

It never types into a terminal for you and never pushes. Each agent runs headlessly (`codex exec`, `claude -p`), one turn at a time. The relay decides whose turn it is by reading [whyline](https://github.com/anishmoncivarghese/whyline)'s handoff record. It does not parse agent output to route.

> **Status:** 0.2. The 0.2 features (`remove`, `--version`, the progress lines and the truthful `status`, the verified-review rule) were built by running this tool on its own plan: Codex implemented, Claude reviewed and committed. It has run on nine real tasks on one macOS machine. Treat it as early software and read the [safety section](#permissions-and-safety) before pointing it at anything valuable.

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Writing a plan](#writing-a-plan)
- [Running it](#running-it)
- [When it pauses](#when-it-pauses)
- [Command reference](#command-reference)
- [Configuration](#configuration)
- [Permissions and safety](#permissions-and-safety)
- [What it writes](#what-it-writes)
- [Removing the relay](#removing-the-relay)
- [What it costs](#what-it-costs)
- [FAQ](#faq)
- [Limitations](#limitations)
- [Development](#development)

## How it works

For each unchecked task in the plan, in file order:

1. **Codex implements.** The relay launches `codex exec` with a prompt made of the task text, whyline's current context, and instructions to run the tests, record decisions, and hand off with `whyline handoff ... --status ready-for-review`. Codex must not commit.
2. **The relay checks Codex did not commit.** If the branch's HEAD moved, it pauses.
3. **Claude reviews.** The relay launches `claude -p` with a review prompt. Claude reads the diff, runs the tests itself, and either commits with the task id in the message and hands off `approved`, or hands off `changes-requested` with concrete feedback. If it cannot run the tests it must hand off `blocked` rather than approve.
4. **On `changes-requested`**, Codex gets the feedback and tries again, up to `max_rounds` (default 3).
5. **On `approved`**, the relay verifies that a commit naming the task exists, that nothing else is left uncommitted, then ticks the plan checkbox and makes one small commit of only `plan.md`.

The control plane is whyline's `.whyline/active-handoff.json`. A new handoff event decides the next move; a missing or unchanged one means the agent stopped without handing off, and the relay pauses. It never guesses a verdict from exit codes or text.

Work happens on its own branch (default `relay/<plan-name>`), created from wherever you are. You review and merge it. Nothing is pushed.

## Requirements

- Python 3.11 or newer, and a git repository to run in.
- [`whyline`](https://github.com/anishmoncivarghese/whyline) installed, and `whyline init` run once in that repository. The relay reads and writes whyline handoffs; without it `start` stops with "whyline is not initialised here".
- The `codex` and `claude` command-line tools, installed and logged in. Tested with `codex-cli 0.155.1` and Claude Code `2.1.278`.
- Your project's own dependencies already installed (for example `uv sync`). Codex runs in a sandbox **without network access**, so it cannot install anything mid-run.

Tested on macOS. Linux runs the test suite in CI. Windows is untested.

## Install

```sh
uv tool install whyline-relay
whyline-relay --version
```

Upgrade with `uv tool upgrade whyline-relay`, remove with `uv tool uninstall whyline-relay`. From a clone: `uv tool install .`.

## Upgrading from 0.1

```sh
uv tool upgrade whyline-relay
```

The program upgrades, but each repository keeps the files `init` wrote earlier, and 0.2 ships an improved review prompt and wider permissions (the reviewer must run the tests itself and may not approve on the implementer's word; read-only helpers such as `tail` and `grep`; `.venv/bin/pytest`). To pick them up in a repository you set up with 0.1:

```sh
cp -r .whyline/relay /tmp/relay-backup      # keep your edits
whyline-relay init --overwrite              # replaces config, prompts and permissions with the 0.2 defaults
# then re-apply your own edits from the backup (timeouts, project test command, ...)
git diff .whyline/relay                     # review what changed, then commit it
```

Or apply just the two changes by hand: add `Bash(tail:*)`, `Bash(head:*)`, `Bash(wc:*)`, `Bash(grep:*)`, `Bash(ls:*)` (and for Python `Bash(.venv/bin/pytest:*)`, `Bash(.venv/bin/python:*)`) to `claude-settings.json`, and copy the "How to review" paragraph from a fresh `init` into `prompts/review.md`.

## Quick start

In the repository you want to work on (use a branch or a throwaway clone the first time):

```sh
cd my-project
uv sync                       # or whatever installs your dependencies: agents cannot, mid-run
whyline init --yes            # once per repository: whyline's own hooks and instruction files
whyline-relay init            # relay config, Claude's permissions, prompt templates
```

Write a plan (see [Writing a plan](#writing-a-plan)), then commit everything, because `start` refuses a dirty working tree:

```sh
cat > plan.md <<'EOF'
- [ ] APP-1: Add a --verbose flag to the CLI
  Add `--verbose` to `myapp.cli`. When set, print each step to stderr.
  Add tests for both the default and the verbose output. Change nothing else.
EOF
git add -A && git commit -m "chore: configure whyline and the relay"
```

Preview, then run:

```sh
whyline-relay start --dry-run   # prints the next task's prompt and the command; launches nothing
whyline-relay start             # the real run. Leave this terminal alone.
```

When it finishes you have a branch to review:

```sh
git log --oneline main..relay/plan
git diff main..relay/plan
```

## Writing a plan

The plan is a Markdown checklist. `plan.md` in the repository root is the default; `--plan PATH` or the `plan` config key change it.

```markdown
# My project

Any prose here is ignored. So are headings.

## Phase 1: foundations

- [x] APP-1: Already done, skipped
- [ ] APP-2: Add the parser
  Implement `parse(text)` in `myapp/parser.py`.
  It returns a list of `Item` objects and raises `ParseError` on bad input.
  Add unit tests, including the error case. Change nothing else.
- [ ] APP-3: Expose it on the command line
  - Add a `parse` subcommand that reads a file and prints the items.
  - Exit 1 with a clear message on `ParseError`.

## Phase 2: features

- [ ] APP-4: ...
```

**The rules**

- A task is a line `- [ ] ID: title`, followed by indented lines with the details. The details end at the next unindented line or the next checkbox.
- **Always start a task with `ID:`.** If you leave it out, the first word silently becomes the id (`- [ ] Add a cache` gets the id `Add`). Ids must be unique; duplicates are rejected.
- `- [x]` tasks are skipped. Tasks run in file order, and each builds on the commits before it.
- Headings, prose and blank lines between tasks are ignored, so use them for your own structure.
- The plan is re-read after every task, so you can append tasks while a run is going. Do not edit a task that is in flight.

**Writing tasks that work**

Each task is handed to a fresh Codex session with no memory of earlier tasks, so it must stand alone. What worked in practice:

- Say what to build, where it goes, and the edge cases that matter.
- Say what tests to add, and what must not change ("change nothing else" prevents scope creep).
- Keep a task to about one reviewable change. Vague or huge tasks are what cost extra review rounds.
- If a behaviour is specified by exact message text or exit codes, quote them.

**Phases**

There is no built-in "stop after phase" gate. To get a review point between phases, use one plan file per phase:

```sh
whyline-relay start --plan phase-1.md --branch relay/work    # ten tasks, then you review
whyline-relay start --plan phase-2.md --branch relay/work    # continues on the same branch
```

Passing the same `--branch` keeps the work stacked. Without it, each plan gets its own branch, `relay/phase-1` and `relay/phase-2`, and a new branch starts from **whatever you have checked out**. So merge phase 1, or `git switch relay/phase-1`, before starting phase 2, or phase 2 will not include phase 1's work.

### Having an AI draft the plan

Run `whyline-relay plan-format` to print the format rules and a paste-ready prompt. To copy only the prompt into another tool, use `whyline-relay plan-format --prompt`.

## Running it

Run `whyline-relay start` in a terminal of its own, not inside an agent session. The relay launches Codex and Claude itself; you do not open them.

While it runs it prints its own progress lines, then the agents' output:

```text
[13:06:44] ==> codex: implementing APP-1 (round 1 of 3)
[13:06:46] ... codex still running (2s)
[13:06:49] <== codex finished in 5s
[13:06:49] ==> claude: reviewing APP-1 (round 1 of 3)
[13:06:55] <== claude finished in 0s
[13:06:55] ==> relay: ticked APP-1 in the plan
Plan complete: 1 task(s) approved and committed.
```

- The `... still running` heartbeat appears after 30 seconds of silence and repeats every 30 seconds. Set `WHYLINE_RELAY_HEARTBEAT_SECONDS` to change it.
- These lines go to the terminal only, never into the log files.
- **Claude's turn is silent until it ends.** Its output arrives as one JSON line at the end, so the heartbeat is the only sign of life. This is normal.
- A finished task shows as two commits: Claude's `feat: ... (APP-1)` and the relay's `chore: tick APP-1 in the plan`.
- On completion or a pause you also get a desktop notification, where the platform supports it (macOS, or `notify-send` on Linux).

**Watching from another terminal**

```sh
whyline-relay status
```

While a run is live this prints, for example, `Running: claude reviewing APP-1, round 1, since 13:06:49 (2m ago)`. If the run is paused it shows the reason and the log path instead. Also useful: `git log --oneline --graph --all`, and `tail -f` on a file in `.whyline/relay/logs/`.

**Stopping**

- `Ctrl+C` in the relay's terminal stops the running agent, saves state, and exits with code 2. `whyline-relay resume` continues.
- `whyline-relay stop` from another terminal lets the current agent finish and starts nothing new.

Only one relay can run in a repository at a time. `start` and `resume` refuse to begin while another live run exists. Separate repositories can run in parallel, but they share your agent quotas.

## When it pauses

The relay stops at the first problem instead of pushing on. It prints `Paused: <reason>`, the log path, and `Resume with: whyline-relay resume`. Fix the cause, then resume; it re-enters at the same decision point (Codex is not re-run if it already handed off).

| The reason says | What it means, and what to do |
|---|---|
| `<agent> exited without handing off; it was denied permission to run: ...` | Claude tried commands the allowlist does not cover. Add them to `.whyline/relay/claude-settings.json`, then `resume`. |
| `<agent> exited without handing off; its last output was: "..."` | The agent stopped without a handoff. The quoted line is usually the cause. Check the log. |
| `<agent> reported blocked: <summary>. Question: <q>` | The agent needs a human decision. The question says what. Answer it (often: add a permission), then `resume`. |
| `codex made a commit, which the relay forbids ... Undo it with git reset <sha>` | Codex committed. Run the given command (your files stay), then `resume`. |
| `<task> was approved and committed, but files are still uncommitted (...)` | Something was left out of Claude's commit. `git stash -u` it (do not commit it), then `resume`. |
| `<task> was approved but no commit naming it exists` | Claude approved without committing, or the message lacks the task id. Commit by hand with the id, or run the task again. |
| `<task> hit the 3-round cap without an approval` | Codex and Claude could not agree. Read the last two logs, then clarify the task in `plan.md` or finish it by hand. |
| `<agent> hit a usage or rate limit` | You ran out of quota. Wait for the reset, then `resume`. |
| `<agent> handed off for '<other task>', but this run is on '<task>'` | The agent mislabelled its handoff. Check the log, then `resume`. |
| `unrecognised handoff status '...'` | An agent used a status the relay does not know. Check the log. |
| `... exceeded <n>s and was terminated` | An agent ran past `timeout_minutes`. Raise it (`--timeout`) or split the task. |
| `... is not installed or not on PATH` | The `codex` or `claude` command was not found. |

**Exit codes:** `0` success, `1` an error or refusal, `2` paused or interrupted.

## Command reference

```text
whyline-relay [--version] <command>
```

**`init`** writes the relay's setup into `.whyline/relay/`, after asking.
`--repo REPO` Use this repository root (default: current directory). `--yes` Skip the confirmation question. `--overwrite` Replace files that already exist, discarding your edits.
It detects a Python project (`pyproject.toml`), a Node project (`package.json`), or neither, and picks the permission preset to match. Declining, or having no terminal, writes nothing.
`init` is safe to re-run. A file that does not exist is written; a file identical to what it would write is left alone; a file that exists and differs is **kept**, with a line such as `Kept .whyline/relay/prompts/review.md: it already exists and differs. Run with --overwrite to replace it.` Pass `--overwrite` to replace every file with the defaults, discarding your edits.

**`start`** runs the plan from its first unchecked task.
`--repo REPO` Use this repository root (default: current directory).
`--plan PLAN` Use this plan file (default: from config).
`--dry-run` Print the next task's prompt and command without launching anything.
`--only TASK_ID` Run only the named task.
`--branch BRANCH` Use this work branch (default: `relay/<plan-name>`).
`--allow-main` Allow running on `main` or `master`.
`--allow-dirty` Skip the clean-working-tree check.
`--max-rounds N` Override the review-round limit from config.
`--timeout MIN` Override the per-agent timeout from config, in minutes.

**`resume`** continues after a pause, on the saved branch, from the saved decision point.
`--repo REPO`, `--allow-dirty` as above.

**`status`** shows whether a run is live, or where a paused one stopped. `--repo REPO`.

**`stop`** lets the current agent finish and starts nothing new. `--repo REPO`.

**`remove`** takes the relay out of a repository; see [Removing the relay](#removing-the-relay).
`--repo REPO`, `--yes`, and `--force` Remove even while a run is paused.

**`plan-format`** prints the plan rules and a prompt for an AI drafting the plan. `--prompt` prints only the paste-ready prompt. It does not require a repository.

## Configuration

`init` writes `.whyline/relay/config.toml`. Every key is optional; anything you omit uses the default shown.

```toml
plan = "plan.md"            # the plan file, relative to the repository root
max_rounds = 3              # review rounds per task before it pauses
timeout_minutes = 30        # per agent turn; the agent's whole process group is killed after this
branch_prefix = "relay/"    # the default work branch is branch_prefix + the plan file's name

[agents.codex]
command = ["codex", "exec", "-s", "workspace-write", "--color", "never"]

[agents.claude]
command = ["claude", "-p", "--permission-mode", "acceptEdits", "--output-format", "json",
           "--settings", ".whyline/relay/claude-settings.json"]

[status_map]                # the handoff status strings the relay routes on
review = "ready-for-review"
changes = "changes-requested"
approved = "approved"
blocked = "blocked"
assigned = "assigned"
```

- The prompt is appended to each command as its last argument.
- The two roles are named `codex` and `claude` in the handoff protocol. You can change the command each one runs, but the names are fixed.
- **Prompts:** `.whyline/relay/prompts/implement.md` and `review.md` are the wrapper prompts sent each turn. They are yours to edit. Placeholders are `{task_id}`, `{task_text}`, `{sync_packet}`, `{round}` and `{review_feedback}`, substituted as plain text (so braces in JSON examples are safe). `--dry-run` shows the assembled Codex prompt.
- **Where instructions reach the agents from:** the task in the plan, the two prompt templates, `AGENTS.md` (whyline's block, which agents read on their own), and, for Claude, the permissions file.

## Permissions and safety

**Read this before using it on anything valuable.**

**Claude's permissions** live in `.whyline/relay/claude-settings.json` and are passed with `--settings`. That is deliberate: Claude Code ignores a project's `.claude/settings.json` permissions under `claude -p` until the workspace has been trusted interactively, so a fresh checkout would silently get none. `init` writes:

- Always: `Edit`; `git add`, `commit`, `diff`, `status`, `log`; `whyline`; and read-only helpers `tail`, `head`, `wc`, `grep`, `ls`.
- Python projects add `pytest`, `uv run pytest`, `uv run`, `.venv/bin/pytest`, `.venv/bin/python`. Node projects add `npm test`, `npm run`, `npx`.
- Always denied: `git push` and `rm -rf`.

Edit the file to add your project's test command so the reviewer can run tests. The allowlist matches the start of a simple command: an environment-variable prefix, a pipe into something not listed, or a `;` chain is denied.

**The allowlist is a convenience, not a security boundary.** Broad entries such as `uv run`, `npm` and `npx` execute arbitrary project code. Run unattended relays on an isolated branch, in a repository (or a fresh clone) that holds no valuable secrets, and read the resulting branch before merging it.

**Codex** runs with `-s workspace-write`: it can write inside the repository, has no network, and can be told to run tests. **That sandbox does not stop it from running `git commit`** (measured on `codex-cli 0.155.1`). The prompt forbids it and the relay checks HEAD after every Codex turn, but that is a check, not a wall.

**What the relay does to protect you**

- Never runs `git push`, and never passes a permission-bypass flag (`--dangerously-*`). Both are enforced by the test suite.
- Refuses to start on `main` or `master` without `--allow-main`, and refuses a dirty working tree without `--allow-dirty`.
- Checks that Codex did not commit, that an approval is backed by a commit naming the task, and that nothing was left uncommitted afterwards.
- Reviews are fail-closed: the review prompt tells Claude not to approve on the implementer's word if it cannot run the tests, but to hand off `blocked` and name the denied command.
- Kills an agent's whole process group on timeout and on Ctrl+C, and refuses to run two relays in one repository.
- Stops at the first problem.

**What it cannot do:** it cannot tell you the reviewer was right. An automated review is lighter than a careful human one, so read the branch.

## What it writes

Everything the relay adds is under `.whyline/relay/`:

| Path | What | In git? |
|---|---|---|
| `config.toml` | settings (above) | yes: commit it |
| `claude-settings.json` | Claude's permissions | yes: commit it, it is reviewable |
| `prompts/` | the two editable prompt templates | yes |
| `logs/` | one file per agent turn, `<task>-<round>-<agent>.log` | no, ignored locally |
| `state.json` | saved progress of a paused run | no, ignored locally |
| `STOP` | written by `whyline-relay stop` | no, ignored locally |
| `running.json` | marks a live run (agent, task, round, pid) | no, ignored locally |

"Ignored locally" means the relay adds those four lines to `.git/info/exclude`, which is never committed, so they neither dirty your tree nor end up in a task's commit.

The relay itself commits exactly one thing: `plan.md`, when it ticks a box (`chore: tick <id> in the plan`). Everything else in git history is Claude's commits, plus your own.

It never changes whyline's files, `AGENTS.md`, `CLAUDE.md`, `.claude/`, or `.codex/`.

## Removing the relay

```sh
whyline-relay remove
```

This deletes `.whyline/relay/` and the four relay lines from `.git/info/exclude`, after listing what will go and asking `Remove these? [y/N]` (default no; no terminal counts as no; `--yes` skips the question). It:

- refuses while a run is paused (`resume` it first, or pass `--force`), and refuses while a run is live, even with `--force`;
- reports how many of the files git tracks, and reminds you the deletions need committing (it never runs `git add` or `git commit`);
- never follows a symlink out of the repository, and never deletes outside it;
- does nothing, and says so, if the relay is not set up.

It leaves whyline's own files, your history, and your branches alone. To uninstall the program itself: `uv tool uninstall whyline-relay`.

## What it costs

Measured on one macOS machine over the nine tasks that built 0.2 (a few dozen to a few hundred lines each):

| | Typical |
|---|---|
| Time per task | 3 to 11 minutes (Codex is most of it) |
| Codex per task | 31k to 96k tokens |
| Claude review per task | $0.15 to $0.26 |
| Review rounds | 1 (every task so far passed first time) |

At those averages a plan of 20 such tasks is roughly two hours, about 1.2 million Codex tokens and about $4 of Claude. Your quotas may pause the run before that; `resume` continues after the reset. These numbers are from a few runs, not a benchmark.

## FAQ

**Can I use it on `main`?** Only with `--allow-main`. By default it works on its own branch.

**What if my plan has 20 tasks in two phases?** Use one file per phase; see [Phases](#writing-a-plan).

**Can I keep working while it runs?** Not in the same working tree. Use a separate clone or worktree. Agents edit the tree and the relay commits to it.

**Does Codex see earlier tasks?** No. Each turn is a fresh session. It sees the task text, whyline's context (recent decisions and the active handoff), and the repository as it now stands.

**Where are decisions recorded?** In `.whyline/decisions.md`, through `whyline note`. The prompts ask both agents to record genuine decisions, and Claude's commit includes them.

**My project needs network to run its tests.** Codex's sandbox has none, so those tests will fail for Codex. Install dependencies beforehand, or exclude network tests from the default command.

**Can I use a different agent?** You can change the command each role runs, but the roles are named `codex` and `claude` in the protocol, and the prompts and permissions assume those two tools.

**It approved something wrong.** It happens; the reviewer is a model. Review the branch before merging, and tighten the task text.

## Limitations

- Tested on macOS with the two tool versions above; Windows is untested.
- No built-in phase gate or dependency graph between tasks: order is file order.
- Claude's turn prints nothing until it ends (JSON output).
- The relay trusts what the agents write to whyline. It verifies the commit and the working tree, not the code's correctness.
- Task ids are not validated for shape; see [the plan rules](#writing-a-plan).
- `resume` re-enters a task at its saved decision point but does not preserve Codex's conversation, which is a fresh session each turn.

## Development

```sh
git clone https://github.com/anishmoncivarghese/whyline-relay
cd whyline-relay
uv sync
uv run pytest
```

The suite never launches a real `codex` or `claude` and never pops a desktop notification; tests use small fake agents. CI runs it on Linux and macOS with Python 3.11 and 3.13. Standard library only: there are no runtime dependencies.

Design and history: the [design specification](https://github.com/anishmoncivarghese/whyline/blob/main/docs/superpowers/specs/2026-09-20-whyline-relay-design.md) and the [implementation plan](https://github.com/anishmoncivarghese/whyline/blob/main/docs/superpowers/plans/2026-09-20-whyline-relay.md) live in the whyline repository.

## License

Apache-2.0. See [LICENSE](LICENSE).
