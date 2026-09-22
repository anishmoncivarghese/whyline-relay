# Antigravity (`agy`) adapter for whyline-relay — asking Antigravity itself

This is a request for your own review, not a spec someone is asking you to implement. Read it, look at the code it points to, and tell the owner what you'd actually build and why, including where you'd disagree with the approach below. You have read access to this repository; do not edit or commit anything.

## What whyline-relay is

It runs a Markdown task plan through two agents in fixed-but-configurable roles: an **implementer** and a **reviewer**. The reviewer runs the tests and is the only one that commits. A small `adapters` package holds what's specific to each tool. Read these to understand the shape an adapter takes:

- `src/whyline_relay/adapters/base.py` — the `Adapter` dataclass and `Manages` flags every adapter fills in.
- `src/whyline_relay/adapters/codex.py` and `src/whyline_relay/adapters/generic.py` — the two simplest adapters, to see the shape filled in.
- `src/whyline_relay/adapters/claude.py` — the most complete one: it also owns a written permissions file and a denial parser.
- `src/whyline_relay/adapters/__init__.py` — the registry.
- `src/whyline_relay/config.py` — how `[roles]` in `config.toml` picks which adapter fills which role.
- `src/whyline_relay/loop.py` — the part that actually runs an agent each turn (`_run_agent`), checks the implementer didn't commit, and reads a denial via `adapter.diagnose(log_text)` when an agent hands off nothing.
- `README.md`, sections "Choosing which agent fills each role" and "Permissions and safety" — what the relay promises the user about every agent it runs.

The rule that matters most: **the relay never passes a permission-bypass flag to any agent, and never widens what an agent is allowed to do.** It can refuse to run a misconfigured command, but it does not get to loosen permissions on an agent's behalf.

## What we already measured about you (2026-09-22, `agy` 1.2.8, OAuth'd to a Google AI subscription)

- Headless: `agy -p "<prompt>" --output-format json --mode accept-edits`. Exit code is always 0, success or denied; routing has to come from the JSON.
- **Everything is denied by default**, including `read_file`, unless allowed. The denial looks like `{"status":"SUCCESS","response":"","denied_actions":[{"action":"command","display_name":"RunCommand"}]}` — the tool type is named, not the specific command or path that was attempted.
- The allow-list lives in `permissions.allow` inside `~/.gemini/antigravity-cli/settings.json` — **one file, global to the machine**, not scoped to a repository or an invocation the way Claude Code's `--settings <path>` is. We found no per-invocation equivalent in `agy help` or `agy --help`.
- Without `--add-dir <root> --new-project`, a headless call was measured operating on a stale prior project and reading a file from the *wrong* directory entirely, not the invocation's cwd.
- `--sandbox` did **not** stop a `git commit` from succeeding.
- It reads `AGENTS.md` and acted on whyline's instructions in it unprompted (ran `whyline sync` on its own).
- No per-token dollar cost; usage is reported as `input_tokens`/`output_tokens`/`thinking_tokens`/`cache_read_tokens` per call, and covered by the subscription.

## What we're actually unsure about, and want your answer to

1. **Is there any way to scope `permissions.allow` to one repository or one invocation**, so a relay running you doesn't have to write to the single global settings file that every other `agy` session on the machine also reads? A relay that edits that file before each task and restores it after is the fallback we're considering, but it's racy against a concurrent interactive `agy` session and it's not scoped to a repository at all, which is worse than what Claude Code gets from `--settings`.
2. **Is there a stricter mode that actually stops a commit**, or any write outside an explicit allow-list, so the relay isn't relying purely on a post-hoc `git` HEAD check the way it already has to for Codex?
3. **Can a denial be made to name the specific command or path it blocked**, not just the tool type? The relay surfaces that detail to a human when a run pauses; for Claude and Codex it can, for you today it can't say more than "a RunCommand call was denied."
4. **Is `--add-dir <root> --new-project` actually the right, supported way to pin a one-shot headless call to a specific repository**, or is there a better-supported flag we're missing? Does `--new-project` leave anything behind that needs cleaning up if the relay calls it once per task, potentially hundreds of times?
5. Anything else about your own headless invocation model that would make it unsafe, unreliable, or just surprising to wrap the way whyline-relay wraps Codex and Claude — running once per task, non-interactively, unattended, with no one available to answer a permission prompt.

## What to tell the owner

Propose a concrete `src/whyline_relay/adapters/antigravity.py`, in the same shape as the existing adapters (`default_command`, `binary`, `login_argv`/`login_fix` if you have a real login-status check, `permission_files`, `diagnose`, `manages`), and say plainly which of the five questions above don't have a good answer yet — those are the ones that decide whether this adapter should exist at all, versus staying a documented limitation.
