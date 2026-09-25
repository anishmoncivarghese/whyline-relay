# whyline-relay

Give it a Markdown plan. It runs each task through an implementer and a reviewer, by default **Codex** (implements) and **Claude** (reviews and commits), one task at a time, unattended, and stops when something needs a human. Since 0.2.2 either role can be filled by any built-in agent, or by another tool you configure yourself; see [Choosing which agent fills each role](#choosing-which-agent-fills-each-role).

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

> **Status:** 0.2. Most 0.2.x features, including the pluggable agents in 0.2.2, backup agents in 0.2.4, model selection in 0.2.6, and a configurable multi-stage `[pipeline]` with crash-safe resume in 0.2.8, were built by running this tool on its own plan: an implementer implemented, a reviewer reviewed and committed. (0.2.3 and 0.2.5 are the exceptions: small fixes found and fixed directly, then validated for real rather than built through the relay.) It has run on real tasks on one macOS machine. Treat it as early software and read the [safety section](#permissions-and-safety) before pointing it at anything valuable.

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Writing a plan](#writing-a-plan)
- [Running it](#running-it)
- [When it pauses](#when-it-pauses)
- [Command reference](#command-reference)
- [Using it from another program](#using-it-from-another-program)
- [Configuration](#configuration)
- [Choosing which agent fills each role](#choosing-which-agent-fills-each-role)
- [Backup agents](#backup-agents)
- [Configuring a custom pipeline](#configuring-a-custom-pipeline)
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
whyline-relay doctor            # checks whyline, both agent logins, the plan and the working tree
whyline-relay start --dry-run   # prints the next task's prompt and the command; launches nothing
whyline-relay start             # the real run. Leave this terminal alone.
```

`start` and `resume` run the same checks themselves and refuse to launch anything if one fails, so a logged-out agent or a missing setup shows up before the run, not in the middle of it. `--skip-checks` bypasses them.

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

### Drafting a plan instead of writing one by hand

`whyline-relay plan "<free-text description>"` drafts a `plan.md` for you: one agent (`[planner].draft`, defaulting to your implementer) writes a candidate plan, a second agent (`[planner].review`, defaulting to your reviewer) checks it for structural problems only — never whether it's the *right* plan, which stays your call — and bounces it back for another draft if something's missing (`[planner].max_visits`, default 3). Once it passes, you're shown the draft and asked to approve it, request changes with feedback (unbounded — keep iterating as long as you like), or discard it. Approving writes and commits the real `plan.md` and offers to run `whyline-relay start` on it immediately.

```toml
[planner]
draft = "codex"     # defaults to your implementer's agent if omitted
review = "claude"   # defaults to your reviewer's agent if omitted
max_visits = 3
```

A plan session checkpoints the same way a running task does — `whyline-relay resume` picks an interrupted draft/review loop, or an unanswered approval gate, back up; `whyline-relay plan --discard` abandons one without resuming it.

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
`--repo REPO` Use this repository root (default: current directory). `--yes` Skip the confirmation question. `--overwrite` Replace files that already exist, discarding your edits. `--implementer NAME`, `--reviewer NAME` Choose a built-in agent (`codex` or `claude`) for the role; see [Choosing which agent fills each role](#choosing-which-agent-fills-each-role).
It detects a Python project (`pyproject.toml`), a Node project (`package.json`), or neither, and picks the permission preset to match. It writes permission files only for the agents actually filling a role, so `--implementer codex --reviewer codex` writes no `claude-settings.json`. Declining, or having no terminal, writes nothing.
`init` is safe to re-run. A file that does not exist is written; a file identical to what it would write is left alone; a file that exists and differs is **kept**, with a line such as `Kept .whyline/relay/prompts/review.md: it already exists and differs. Run with --overwrite to replace it.` Pass `--overwrite` to replace every file with the defaults, discarding your edits. Run it again with `--overwrite` after changing `[roles]` in an existing setup, so the prompt templates name the new roles; `doctor` catches a stale template and tells you to.

**`start`** runs the plan from its first unchecked task.
`--repo REPO` Use this repository root (default: current directory).
`--plan PLAN` Use this plan file (default: from config).
`--dry-run` Print the next task's prompt and command without launching anything.
`--only TASK_ID` Run only the named task.
`--branch BRANCH` Use this work branch (default: `relay/<plan-name>`).
`--allow-main` Allow running on `main` or `master`.
`--allow-dirty` Skip the clean-working-tree check.
`--skip-checks` Skip the preflight checks.
`--max-rounds N` Override the review-round limit from config.
`--timeout MIN` Override the per-agent timeout from config, in minutes.

**`resume`** continues after a pause, on the saved branch, from the saved decision point.
`--repo REPO`, `--allow-dirty`, and `--skip-checks` as above.

**`doctor`** runs the same preflight checks used by `start` and `resume`, reports every result, and exits 1 if any check fails.
`--repo REPO` Use this repository root (default: current directory). `--plan PLAN` Use this plan file (default: from config). `--allow-dirty` Skip the clean-working-tree check.

**`status`** shows whether a run is live, or where a paused one stopped. `--repo REPO`.

**`stop`** lets the current agent finish and starts nothing new. `--repo REPO`.

**`remove`** takes the relay out of a repository; see [Removing the relay](#removing-the-relay).
`--repo REPO`, `--yes`, and `--force` Remove even while a run is paused.

**`plan-format`** prints the plan rules and a prompt for an AI drafting the plan. `--prompt` prints only the paste-ready prompt. It does not require a repository.

**`roles status`** shows each role's configured agent, and, if a backup has taken over, which one and why. **`roles reset [ROLE]`** clears a role's backup switch (or every role's, with no argument), reverting to the configured agent. Both take `--repo REPO`. See [Backup agents](#backup-agents).

**`whyline-relay roles set <ROLE> [--agent NAME] [--model NAME]`** permanently points a role at an agent — a real edit to `config.toml`, unlike `roles reset`, which only clears a temporary failover switch. Works for any role the current config defines: `implementer`/`reviewer` normally, or a configured `[pipeline]`'s own role names. Called with no `--agent`/`--model`, it prompts for both interactively (blank keeps the current agent; a model prompt only appears for a built-in agent name). Refuses an agent name that isn't a built-in or an already-configured one, the same validation `config.toml` itself is held to.

## Using it from another program

Call `whyline_relay.cli.main` with the command arguments and the name users invoked:

```python
from whyline_relay.cli import main

exit_code = main(["status", "--repo", "."], prog="whyline relay")
```

`main` is the supported embedding entry point. It returns the relay's exit code and never calls `sys.exit`. When `argv` is provided, it parses that list without reading `sys.argv`. The `prog` value appears in help output and in every instruction that tells the user how to invoke another relay command.

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

[roles]                     # since 0.2.2; both optional, these are the defaults
implementer = "codex"
reviewer    = "claude"
```

- The prompt is appended to each command as its last argument.
- **Prompts:** `.whyline/relay/prompts/implement.md` and `review.md` are the wrapper prompts sent each turn. They are yours to edit. Placeholders are `{task_id}`, `{task_text}`, `{sync_packet}`, `{round}`, `{review_feedback}`, and, since 0.2.2, `{implementer}` and `{reviewer}` (the two agent names, so a template stays correct if you change `[roles]`), substituted as plain text (so braces in JSON examples are safe). Since 0.2.8, a custom `[pipeline]` stage's own prompt also gets `{actor}`, `{role}`, `{stage}`, and `{profile}` — see [Configuring a custom pipeline](#configuring-a-custom-pipeline). `--dry-run` shows the assembled prompt for whichever agent implements.
- **Where instructions reach the agents from:** the task in the plan, the two prompt templates, `AGENTS.md` (whyline's block, which agents read on their own), and, for an agent whose permissions the relay manages, the permissions file.

## Choosing which agent fills each role

By default the implementer is `codex` and the reviewer is `claude`; nothing here changes unless you set `[roles]`. Since 0.2.2, either role can be:

- **A built-in agent**, `codex` or `claude`, in either role. `init --implementer claude --reviewer codex` sets both up, writing only the permission file the agents in use need (here, still `claude-settings.json`, since Claude fills a role either way). The same agent can fill both roles (`implementer = "claude"` and `reviewer = "claude"`); `doctor` then warns that the review is not independent, but does not stop you.

Run `init` with neither flag, in a real terminal, and it asks instead: which agent for implementer and reviewer (blank keeps `codex`/`claude`), then, since 0.2.12, an optional model for each — the same interactive wizard `roles set` already gave you for changing a role later. `init --yes` (used by every scripted or CI setup) never asks anything and keeps producing exactly what it always has.

- **A generic agent** — any other headless command, run the same way the built-ins are: with the prompt appended as its last argument. Configure it explicitly; there is no default and no guessing:

  ```toml
  [roles]
  reviewer = "aider"

  [agents.aider]
  adapter = "generic"
  command = ["aider", "--message"]
  ```

  A generic agent must be able to run `whyline handoff` and `git` in a shell, from whatever permissions its own tool grants; the relay does not manage its permissions, its login, or how it reports a denial, and `doctor` says so. It inherits the relay's environment, including any API keys the shell has. Writing `adapter = "generic"` is your acceptance of that; there is no way to opt in silently. A tool that cannot take its instructions as a trailing command-line argument (for example, one that only reads a prompt from stdin or a file) cannot be used this way.

### Choosing a model

Since 0.2.6, a name doesn't have to be `codex` or `claude` itself to get their full managed behaviour (login checks, permission-bypass refusal, denial parsing) — it can *alias* one, with its own command and model:

```toml
[roles]
implementer = "claude-opus"
tester      = "claude-haiku"

[agents.claude-opus]
adapter = "claude"
model   = "opus"

[agents.claude-haiku]
adapter = "claude"
model   = "haiku"
```

`adapter` here names which built-in the alias uses — `codex` or `claude` — and is checked, PATH-checked, and bypass-checked exactly as if you'd written that name directly; `claude-opus` and `claude-haiku` above are both fully managed `claude`, not `generic`. `model` is optional and is turned into the real flag each tool already has (`codex exec -m/--model`, `claude --model`, accepting an alias like `opus`/`sonnet`/`haiku` or a full model name) — confirmed against each tool's own `--help`, not guessed. It works on the literal built-in names too, with no alias needed: `[agents.claude] model = "haiku"` just adds `--model haiku` to Claude's own default command. Nobody checks whether your account can actually use the model you pick; an unavailable one fails at run time; a `generic` agent can't take `model` at all — put the flag directly in its `command` yourself, the same way you always could.

Whichever agent runs, the handoff record names it: `--from antigravity --to claude` if `antigravity` were configured, for instance, so `.whyline/decisions.md` and the commit history say which model actually did the work. The relay checks this: if a handoff is recorded under a name other than the agent that just ran, it pauses rather than accept it. After changing `[roles]` on a repository set up before 0.2.2 (or with older prompt templates), run `init --overwrite` so the templates use the new names; `doctor` will tell you to if you forget.

Only `codex` and `claude` are built in today. For a third option, see "Using Antigravity today" just below.

### Using Antigravity (`agy`) today, via the generic adapter

Google's Antigravity CLI (`agy`) works headlessly and can be used right now as a generic agent, with no relay code changes. It is not a built-in, on purpose: it has no way to scope its permissions to a repository or an invocation, no login-status command `doctor` could check, and its denial output names only a tool type ("a RunCommand call was denied"), never the command or path — three real gaps that would make a "built-in" label dishonest about what the relay actually manages for it. This is also tracked upstream at [google-antigravity/antigravity-cli#548](https://github.com/google-antigravity/antigravity-cli/issues/548) — headless mode's own permission enforcement is known to be unreliable; `doctor` links here directly if you configure Antigravity this way. Configure it like this:

```toml
[roles]
implementer = "antigravity"   # or reviewer; either role works
reviewer    = "claude"

[agents.antigravity]
adapter = "generic"
command = ["agy", "--output-format", "json", "--mode", "accept-edits", "--new-project", "--add-dir", ".", "-p"]
```

**`-p` must be the last item in the command.** The relay always appends the prompt as the command's final argument. `agy -p` greedily consumes whatever token comes right after it, so `-p` anywhere earlier makes it swallow the next flag instead of the real prompt, and the turn silently does nothing.

**It denies everything by default, including reads**, unless the repository is in `trustedWorkspaces` and the tool is in `permissions.allow`, both in one file global to the machine: `~/.gemini/antigravity-cli/settings.json`. There is no per-repository or per-invocation override. A working setup needs something like:

```json
{
  "trustedWorkspaces": ["/path/to/your/repository"],
  "permissions": {"allow": ["read_file(*)", "write_file(*)", "edit_file(*)", "command(*)"]}
}
```

Narrower patterns such as `command(git)` were tried and did not work as their own error messages imply; `command(*)` is what was actually verified. Because it is global, this loosens every `agy` session on the machine, not just relay runs.

**It can end a turn while work it started is still running.** Measured once: asked to "run the project's tests" with no further guidance, it started something in the background, reported success, and the CLI exited before that work finished — no handoff, nothing committed, the background task killed on exit. The fix that held up under a real side-by-side comparison: add a line to `.whyline/relay/prompts/implement.md` telling it explicitly not to background anything:

```
Implement the task. Run every command to completion and read its result before
moving on or finishing your turn: never run anything in the background, and never
end your turn while something you started is still running. Then note the exact
test command and its result — you will report both in your handoff.
```

(This replaces the built-in template's shorter "Run the project's tests and note the exact command and its result" opening line under "How to finish".) A comparison run confirmed this beats telling the task itself not to run tests: half the tokens, and it actually ran the real tests instead of only reading files back.

**One more thing worth knowing:** `--new-project`, needed to pin each headless call to the right directory (without it, one call was measured operating on a stale prior project and reading the wrong file entirely), leaves a conversation directory behind under `~/.gemini/antigravity-cli/brain/<uuid>/` every single task, with no built-in cleanup.

## Backup agents

Since 0.2.4, a role can name one backup agent, used automatically when the role's active agent hits a detected usage limit or stops being authenticated:

```toml
[roles.backup]
implementer = "antigravity"
# reviewer has no backup here — a rate limit or auth loss on claude pauses, exactly as before
```

A backup is validated exactly like a role in `[roles]`: it must be a built-in agent or a configured generic one, and it cannot name the same agent already filling that role.

**The switch is sticky.** Once the backup takes over, it stays the role's effective agent — for the rest of that task, and every task after — until you change it, because no agent measured so far reliably reports when a quota actually resets. `whyline-relay roles status` shows whether a role is currently on its backup, and why; `whyline-relay roles reset [ROLE]` reverts it (or every role, with no argument).

**Two triggers, and only these two.** A detected rate limit switches, using the same text-marker check the relay has always used. So does a detected loss of authentication — checked by re-running the agent's own login-status command (`codex login status`, `claude auth status`), never by guessing at output text; a generic agent has no such command, so it can never trigger this path. Nothing else switches an agent: a missing binary, a misconfigured command, or a repeated permission denial are configuration problems, and papering over one by silently switching agents would hide a bug instead of surfacing it.

**Preflight checks a configured backup too**, not only the primary — on PATH, and, for a built-in agent, logged in — so a backup that can't run is caught before it's ever needed, not discovered mid-plan. A backup that is *also* already someone's primary is reported as that primary; it's already fully checked either way.

**If the backup also fails**, there's no further fallback (one hop only): the run pauses, naming both agents and what each one hit.

## Configuring a custom pipeline

Since 0.2.8, `[pipeline]` can replace the fixed implementer/reviewer pair with any number of named stages, each with its own outcome vocabulary and rejection target — a real tester stage that can send work back to the implementer, for instance, not just approve or request changes:

```toml
[roles]
implementer = "codex"
tester      = "claude"
reviewer    = "claude"

[pipeline]
default_profile = "full"

[pipeline.profiles]
full  = ["draft", "test", "review"]
quick = ["draft", "review"]           # a task can pick this with relay-profile: quick

[pipeline.stages.draft]
role   = "implementer"
prompt = "implement"                  # .whyline/relay/prompts/implement.md, or the built-in
[pipeline.stages.draft.on]
ready = "@next"

[pipeline.stages.test]
role       = "tester"
prompt     = "test"                   # not built in -- you must write prompts/test.md yourself
max_visits = 5                        # default 3; a stage bouncing past this pauses the run
[pipeline.stages.test.on]
passed = "@next"
failed = "draft"

[pipeline.stages.review]
role   = "reviewer"
prompt = "review"
[pipeline.stages.review.on]
approved = "@complete"
rejected = "draft"
```

A stage's `on` table maps the outcome your prompt tells the agent to hand off with to what happens next: another stage's id, `"@next"` (the following stage in whichever profile is actually running — the same stage can mean a different "next" in `full` versus `quick`), `"@complete"` (the task is done), or `"@blocked"` (a human is needed). Every profile must have some path to `"@complete"`, checked at config-load time, not discovered mid-run.

**A task picks its profile with a `relay-profile:` line** in its detail, falling back to `default_profile` if it doesn't:

```markdown
- [ ] T-2: a task that skips testing
  relay-profile: quick
```

**Four prompts are built in**: `implement`, `review`, and, since 0.2.10, `test` and `security` — a real tester (judges whether tests are genuine coverage, not tautological, and may add real ones itself) and a real security reviewer (checks for injected input, leaked secrets, and over-broad permissions), each profile-gated like any other stage: include `"test"` or `"security"` in a profile's stage list, or don't. Anything else is yours to write under `.whyline/relay/prompts/<name>.md`; naming a stage prompt with no built-in and no override file is a `doctor` failure, not a runtime surprise.
**Every configured-pipeline stage's prompt gets a relay-generated routing footer**, appended after its own text (built-in or yours): the exact permitted outcomes for *this* stage, the exact agent to address each one to, and the literal `whyline handoff` command to run. A built-in or custom prompt's own text never needs to hardcode a `--to` name or an outcome string, and a hand-edited, stale template can no longer send a handoff to the wrong place — the footer is generated fresh every turn from the pipeline actually configured right now, not typed once and left to rot. Custom prompts get four more placeholders beyond the usual ones: `{actor}` (the agent running), `{role}`, `{stage}`, and `{profile}`.

**No stage commits — the relay does.** Every stage's turn is HEAD-checked, including the terminal one: an agent that runs `git commit` itself is paused with an exact recovery command. Once the terminal stage hands off its accepted outcome, the relay stages everything and makes the one commit that finishes the task itself, using that handoff's `--summary` as the message. This removes an entire class of bug (a committing agent's own judgment being wrong) rather than only guarding against it.

**Crash-safe by design:** a pause mid-pipeline saves exactly which stage and how many times each stage has run; `resume` picks up there, never re-running a stage whose handoff already exists, never silently skipping one either. If `[pipeline]` changes between a pause and a `resume`, the relay refuses to guess and asks you to resolve it by hand.

**Not supported together with `[pipeline]`:** `[roles.backup]` (a configured pipeline has no failover — a stage with no working agent pauses) and a hand-written `[status_map]` (a pipeline's stages define their own outcome labels instead). Both are refused at config-load time with an explanation. There is no `init`/`roles set` wizard for authoring a `[pipeline]` table yet — write it by hand, matching the shape above.

## Permissions and safety

**Read this before using it on anything valuable.**

**Claude's permissions** live in `.whyline/relay/claude-settings.json` and are passed with `--settings`. That is deliberate: Claude Code ignores a project's `.claude/settings.json` permissions under `claude -p` until the workspace has been trusted interactively, so a fresh checkout would silently get none. `init` writes:

- Always: `Edit`; `git add`, `commit`, `diff`, `status`, `log`; `whyline`; and read-only helpers `tail`, `head`, `wc`, `grep`, `ls`.
- Python projects add `pytest`, `uv run pytest`, `uv run`, `.venv/bin/pytest`, `.venv/bin/python`. Node projects add `npm test`, `npm run`, `npx`.
- Always denied: `git push` and `rm -rf`.

Edit the file to add your project's test command so the reviewer can run tests. The allowlist matches the start of a simple command: an environment-variable prefix, a pipe into something not listed, or a `;` chain is denied.

**The allowlist is a convenience, not a security boundary.** Broad entries such as `uv run`, `npm` and `npx` execute arbitrary project code. Run unattended relays on an isolated branch, in a repository (or a fresh clone) that holds no valuable secrets, and read the resulting branch before merging it.

**Codex** runs with `-s workspace-write`: it can write inside the repository, has no network, and can be told to run tests. **That sandbox does not stop it from running `git commit`** (measured on `codex-cli 0.155.1`). The prompt forbids it and the relay checks HEAD after every implementer turn, whichever built-in agent that is, but that is a check, not a wall.

**A generic agent's permissions are its own tool's**, not the relay's. The relay does not add, remove, or inspect them; it can only refuse to launch a command containing one of the flags below.

**What the relay does to protect you**

- Never runs `git push`, and never passes or accepts a permission-bypass flag on a built-in agent's command: Codex's `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`, and a sandbox of `danger-full-access`; Claude's `--dangerously-skip-permissions` and `--permission-mode bypassPermissions`. A configured command containing one is refused by `doctor`, and by `start` even with `--skip-checks`, before anything is launched. All enforced by the test suite. A generic agent's command cannot be inspected this way.
- Refuses to start on `main` or `master` without `--allow-main`, and refuses a dirty working tree without `--allow-dirty`.
- Checks that the implementer did not commit, that an approval is backed by a commit naming the task, and that nothing was left uncommitted afterwards.
- Checks that a handoff was recorded under the name of the agent that actually just ran.
- Reviews are fail-closed: the review prompt tells the reviewer not to approve on the implementer's word if it cannot run the tests, but to hand off `blocked` and name the denied command.
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
| `logs/` | one file per agent turn, `<task>-<round>-<agent>.log` (`-implementer`/`-reviewer` added when one agent fills both roles, so the two turns don't share a file) | no, ignored locally |
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

**Can I use a different agent?** Since 0.2.2, yes: swap which built-in agent, `codex` or `claude`, fills which role in `[roles]`, or configure any other headless tool as a `generic` agent — Antigravity (`agy`) is a documented, working example. See [Choosing which agent fills each role](#choosing-which-agent-fills-each-role).

**Can I pick which model an agent uses?** Since 0.2.6, yes — see [Choosing a model](#choosing-a-model). `[agents.claude] model = "opus"` works directly, or give a variant its own name (`claude-opus`, `claude-haiku`) to use different models for different roles while each stays fully managed.

**One of my agents ran out of quota mid-plan.** Since 0.2.4, configure a backup for that role (`[roles.backup]`) and the relay switches to it automatically and keeps going; see [Backup agents](#backup-agents). Without one, it pauses with a message naming the agent and the limit, same as before.

**It approved something wrong.** It happens; the reviewer is a model. Review the branch before merging, and tighten the task text.

## Limitations

- Tested on macOS with the two tool versions above; Windows is untested.
- No built-in phase gate or dependency graph between tasks: order is file order.
- Claude's turn prints nothing until it ends (JSON output).
- The relay trusts what the agents write to whyline. It verifies the commit and the working tree, not the code's correctness.
- Task ids are not validated for shape; see [the plan rules](#writing-a-plan).
- `resume` re-enters a task at its saved decision point but does not preserve the implementer's conversation, which is a fresh session each turn.
- Only `codex` and `claude` are built in. A `generic` agent's login, permissions, quota, and how it reports a denied command are entirely its own; the relay does not manage or check any of them.

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
