# Grok Build (`grok`) adapter for whyline-relay — asking Grok itself

This is a request for your own review, not a spec someone is asking you to implement. Read it, look at the code it points to, and tell the owner what you'd actually build and why, including where you'd disagree with the approach below. You have read access to this repository; do not edit or commit anything.

## What whyline-relay is

It runs a Markdown task plan through two (or more) agents in named roles — most commonly an **implementer** and a **reviewer**. The reviewer runs the tests and is the only one that commits, in the simple two-role shape; a configured multi-stage pipeline exists too but is not this consultation's focus. A small `adapters` package holds what's specific to each tool. Read these to understand the shape an adapter takes:

- `src/whyline_relay/adapters/base.py` — the `Adapter` dataclass and `Manages` flags every adapter fills in.
- `src/whyline_relay/adapters/codex.py` — the simplest real adapter, fully managed.
- `src/whyline_relay/adapters/claude.py` — the most complete one: it also owns a written permissions file and a denial parser.
- `src/whyline_relay/adapters/generic.py` — the escape hatch for a tool with no real adapter.
- `src/whyline_relay/adapters/__init__.py` — the registry.
- `src/whyline_relay/config.py` — how `[roles]` in `config.toml` picks which adapter fills which role.
- `src/whyline_relay/loop.py` — the part that actually runs an agent each turn (`run_agent`), checks the implementer didn't commit, and reads a denial via `adapter.diagnose(log_text)` when an agent hands off nothing.
- `src/whyline_relay/failover.py` — automatic switch-to-a-backup-agent on a detected rate limit or lost auth. Read this one carefully; see the failover question below.
- `README.md`, sections "Choosing which agent fills each role" and "Permissions and safety" — what the relay promises the user about every agent it runs. Also read "Using Antigravity today, via the generic adapter" — Antigravity is the other agent added after the original two, and it stayed a `generic` (unmanaged) agent specifically because headless mode does not reliably respect its own `permissions.allow` file for narrow scopes (only a machine-global wildcard was verified to work) — see [google-antigravity/antigravity-cli#548](https://github.com/google-antigravity/antigravity-cli/issues/548).

The rule that matters most: **the relay never passes a permission-bypass flag to any agent, and never widens what an agent is allowed to do.** It can refuse to run a misconfigured command, but it does not get to loosen permissions on an agent's behalf.

## What we already measured about you (2026-09-25, `grok` 1.0.41, SuperGrok subscription)

- Headless: `grok -p "<prompt>" --output-format json --permission-mode dontAsk`. Real, structured JSON response with `stopReason`, token usage, and `total_cost_usd` per call.
- **Unlike Antigravity, narrow per-invocation permission scoping actually works**: `--allow "Write(greet.py)" --allow "Bash(python3:*)"` correctly permitted exactly a scoped write and a scoped command, completed the real work, and a *different*, out-of-scope command in the same permission set (`rm -rf ...`) was still correctly cancelled (`"stopReason": "cancelled"`) rather than either silently succeeding or hanging.
- `--permission-mode dontAsk` is deny-by-default for anything not explicitly allowed: a write attempted with no matching `--allow` rule was cleanly cancelled, and the file was verified untouched afterward — no partial write, no hang.
- `--permission-mode bypassPermissions` also exists (a full bypass, like Codex's `--dangerously-bypass-approvals-and-sandbox` or Claude's `--dangerously-skip-permissions`) — never to be used here.
- `grok login` (no flags) triggers a live device-code OAuth flow rather than only checking status — there is no separate read-only "am I logged in" command we found (unlike `codex login status` / `claude auth status`).
- `grok models` confirms login and lists available models but does not name a subscription tier (SuperGrok vs. X Premium+ vs. higher tiers) in a way we could find to parse.
- `grok inspect` reads `.claude/settings.local.json`'s `permissions.allow` entries directly, suggesting some Claude Code settings-format compatibility, though we have not confirmed whether writing a project-scoped Grok-specific settings file (as opposed to per-invocation `--allow`/`--deny` flags) is the intended mechanism for a repo-scoped policy.

## What we're actually unsure about, and want your answer to

1. **Is there a real login-status check** (something read-only, safe to run in `doctor`, that reports whether `grok` is authenticated and working) — or is `grok login` really the only way, and if so, is it safe to assume "already logged in" sessions don't re-trigger the device flow when run non-interactively with cached credentials present?
2. **Is `--allow`/`--deny` (or a project-scoped settings file) reliably enough for us to build the *same* kind of per-repository, per-invocation permission story Claude Code's `--settings <path>` already gives us** — scoped to exactly this relay's own working tree, not global to the machine the way Antigravity's only working option is? We'd want to write the narrowest rule set an implementer or reviewer turn actually needs (read the diff, write files in the repo, run the test command, run `git`/`whyline` commands) and nothing broader.
3. **Can a cancelled/denied action be made to report which specific rule or command it needed**, the way Codex/Claude's own denial text already does for us — or does "cancelled" stay as generic as Antigravity's "a RunCommand call was denied"?
4. **Can you report which plan/tier your account has** (SuperGrok / X Premium+ / SuperGrok Heavy / SuperHeavy), the way `claude auth status`'s `subscriptionType` field already does for us — needed for a separate feature (`whyline account`/`whyline model`, described in `docs/superpowers/specs/2026-09-25-account-and-model-selector-design.md` if you want the full context) that shows a detected plan as context when picking a model?
5. **The owner explicitly wants resilience against any single tool's usage limit or outage** — today a role can name exactly one backup agent (`failover.py`), switched to automatically on a detected rate limit or lost auth; more than one backup per role was an explicit non-goal when that was designed. Now that there are four real agent choices (Codex, Claude, Antigravity, and potentially you), does this non-goal still make sense, or would you design failover differently with four real options instead of two? What would *you* want us to detect as "this agent is out of usage" for you specifically — is there a text marker in your own rate-limit response, the way Codex/Claude's existing detection already looks for one?
6. Anything else about your own headless invocation model that would make it unsafe, unreliable, or just surprising to wrap the way whyline-relay wraps Codex and Claude — running once per task, non-interactively, unattended, with no one available to answer a permission prompt.

## What to tell the owner

Propose a concrete `src/whyline_relay/adapters/grok.py`, in the same shape as the existing adapters (`default_command`, `binary`, `login_argv`/`login_fix` if you have a real login-status check, `permission_files`, `diagnose`, `manages`, `model_flag`), and say plainly which of the six questions above don't have a good answer yet — those are the ones that decide whether this should be a fully managed built-in adapter (like Codex/Claude) or stay a `generic`-only recipe (like Antigravity today).
