# Generalizing whyline-relay to N named roles — asking you for your own architectural take

This is a request for your own design opinion, not a spec someone is asking you to implement. Read this and the code it points to, then tell the owner what you'd actually build, where you'd disagree with the framing below, and what you think is genuinely hard about it. You have read access to this repository; do not edit or commit anything.

## What whyline-relay is, today (relay 0.2.5)

It runs a Markdown task plan through exactly **two** roles: an **implementer** and a **reviewer** (the reviewer also commits). Read these to see the current shape precisely:

- `src/whyline_relay/routing.py` — `decide()`, the entire state machine, a pure function keyed on `(to_actor, status)`. Five fixed statuses: `ready-for-review`, `changes-requested`, `approved`, `blocked`, `assigned` (configurable strings, but exactly these five slots, `status_map` in `config.py`). Routing hard-codes that only `reviewer` may send `ready-for-review`→`REVIEW` and only `implementer` may send `changes-requested`/`assigned`→`IMPLEMENT`; `approved`/`blocked` apply regardless of recipient.
- `src/whyline_relay/handoff.py` — the `Handoff` record whyline itself writes and the relay only reads: `event_id, task, to_actor, status, summary, questions, from_actor`. No place for a role name distinct from an agent name, and no place for "which stage of the pipeline."
- `src/whyline_relay/config.py` — `Roles(implementer, reviewer)`, a two-field dataclass; `[roles]` and `[roles.backup]` (0.2.4) both key on exactly these two field names.
- `src/whyline_relay/loop.py` — `_run_task`'s `while True:` loop: `role = "implementer" if next_move == routing.IMPLEMENT else "reviewer"`, a binary choice, then `agent = implementer if role == "implementer" else reviewer`.
- `src/whyline_relay/prompts.py` — exactly two built-in templates (`implement.md`, `review.md`), with `{implementer}`/`{reviewer}` placeholders substituted into both.
- `src/whyline_relay/adapters/` — agent adapters (`codex`, `claude`, `generic`) are already role-agnostic; they don't know or care what role they're filling. This part should not need to change.
- `README.md`, section "Backup agents" — the most recently shipped feature, sitting on top of exactly this two-role model (`[roles.backup]`).

## What the owner actually wants

More than two roles. The ones discussed so far, from a real SDLC: **planner** (turns a rough idea into `plan.md`, with a review gate before the detailed task breakdown proceeds — human mode needs an explicit yes/no, auto mode proceeds after a bounded number of rounds), **tester** (writes tests independently of the implementer, interleaved with it — not concurrent, no shared-worktree races), **documentation** and **security-review** (likely thin, mostly differentiated by prompt content), alongside the existing **implementer** and **reviewer** (which may or may not still be the one that commits — that's an open question, not a given).

## What we already know is hard, from having built the two-role version

1. **The five-status vocabulary is really "N-1 handoffs in a straight line plus two universal terminal states."** `approved` and `blocked` apply regardless of recipient; everything else is a specific (status, recipient) pair meaningful only between two adjacent stages. Generalizing this to an arbitrary number of stages, possibly branching (does review ever go back to the planner, not just the implementer?), is the crux of the problem.
2. **Backward compatibility is not optional.** Every existing `config.toml` with just `[roles]` (implementer/reviewer) and every existing `[roles.backup]` must keep working with zero changes, exactly the same bar every past release here has held itself to (verified by running the *old* test suite unedited against each new version).
3. **The reviewer currently also commits.** If commit becomes its own thing (a distinct stage, or a capability separate from "reviewing"), that's a real behavior change to the one thing in this whole system that's genuinely dangerous to get wrong (the relay never lets an implementer commit, checked via a `git` HEAD comparison after every implementer turn — whatever replaces this needs an equivalent guarantee for whichever role is allowed to write to git).
4. **A generic agent adapter doesn't know what role it's in** — it just gets a rendered prompt and appends the response as `--from <name> --to <name> --status <status>`. Any solution has to keep working through that same narrow interface; it cannot require an agent to understand a new protocol concept it wasn't told about explicitly in its prompt.
5. **Not every task needs every role.** A trivial one-line fix arguably doesn't need a security reviewer or even a dedicated tester turn. Whatever config generalizes `[roles]` needs to express "this pipeline has these N stages," not force every task through every stage always.

## What we're actually asking you to propose

A concrete design for generalizing `routing.decide()`, the config schema, and the `Handoff` record's meaning, to support an arbitrary, configured sequence of named roles — while keeping every existing two-role config working with **zero changes to its behavior or its file contents**. Be specific about:

- What replaces the five fixed status strings, and whether `status_map` survives at all in its current form.
- Whether the pipeline is a strict linear sequence, or needs branching/looping (and if so, where exactly — does anything other than "back to the implementer" need to exist?).
- How an optional stage (skip testing for a trivial task) is expressed without the relay having to guess.
- What changes about who is allowed to commit, and how the "the implementer never commits" guarantee generalizes (or doesn't) to N roles.
- Where you'd draw the line between "core relay change" and "just write a longer task prompt" — i.e., which of the candidate roles (planner, tester, documentation, security-review) actually need a first-class pipeline stage versus being achievable today by writing a more detailed task in the existing two-role loop.

Tell the owner plainly which parts of this you think are genuinely architecturally hard, and which parts of the ask you think are simpler than they look.
