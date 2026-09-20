# whyline-relay

`whyline-relay` runs a Markdown implementation plan through an unattended Codex-to-Claude review loop, using whyline handoffs as the control plane. Codex implements but never commits, Claude reviews and commits approved work, and a human remains responsible for pushing it.

## Requirements

- Python 3.11 or newer, and a git repository to run in.
- [`whyline`](https://github.com/anishmoncivarghese/whyline) installed, and `whyline init` run once in that repository. The relay reads and writes whyline handoffs, so without it `start` stops with "whyline is not initialised here".
- The `codex` and `claude` command-line tools, installed and logged in. Tested with `codex-cli 0.155.1` and Claude Code `2.1.278`.

## Install

```sh
uv tool install .
```

## Commands

```sh
whyline-relay init [--repo PATH] [--yes]
whyline-relay start [--repo PATH] [--plan PATH] [--only TASK_ID]
whyline-relay resume [--repo PATH]
whyline-relay status [--repo PATH]
whyline-relay stop [--repo PATH]
```

`init` writes configuration, permission settings, and editable prompt templates below `.whyline/relay/`. Commit those files, and whyline's own, before running `start`; `start` refuses a dirty working tree unless you pass `--allow-dirty`.

`start` also takes `--dry-run` (print the next task's prompt and the command it would run, and launch nothing), `--branch`, `--allow-main`, `--allow-dirty`, `--max-rounds` and `--timeout`. `resume` takes `--allow-dirty`.

## Plan format

The plan is a Markdown checklist. Each task begins with an unchecked item containing an identifier and title; indented content belongs to that task.

```markdown
- [ ] APP-1: Add the parser
  Implement the parser and its unit tests.
- [ ] APP-2: Expose the command
  Add the CLI entry point and integration tests.
```

For example:

```sh
cd my-project
whyline init --yes        # once per repository: whyline's own hooks and instruction files
whyline-relay init        # relay configuration, permissions and prompt templates
git add -A && git commit -m "chore: configure whyline and the relay"
whyline-relay start --plan docs/plan.md --branch relay/parser
```

The relay creates or switches to the work branch, asks Codex to implement the next unchecked task, then asks Claude to review it. Approved work is committed by Claude. The relay then checks that nothing else was left uncommitted, ticks the plan checkbox, and makes one mechanical commit of only the plan file (`chore: tick <id> in the plan`), so the tree is clean after every task. The loop proceeds until the plan is complete or pauses for human input.

## Permissions and safety

Claude permissions live in `.whyline/relay/claude-settings.json` and are passed to Claude with `--settings`. Edit that file to allow the project's test command so the reviewer can run tests. Existing `.claude/settings.json` files are left unchanged.

No permission-bypass flag is ever used. Codex hooks are optional because every relay prompt embeds the output of `whyline sync`.

Codex's sandbox (`-s workspace-write`) does not stop it running `git commit`; this was measured on `codex-cli 0.155.1`. The prompt forbids it, and the relay checks after every Codex turn that HEAD did not move, pausing with the command to undo it if it did.

The permission allowlist is an operational convenience, not a security boundary. Broad entries such as `uv run`, `npm`, and `npx` execute arbitrary project code, so unattended runs belong on an isolated branch or repository that contains no valuable secrets.

The human reviews the resulting branch and pushes it; the relay never pushes.

See the [whyline relay design specification](https://github.com/anishmoncivarghese/whyline/blob/main/docs/superpowers/specs/2026-09-20-whyline-relay-design.md) for the complete architecture and safety model.
