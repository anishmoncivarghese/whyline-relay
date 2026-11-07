"""Default commands for the agents the relay runs through the generic
adapter. They are the README's verified recipes ("Using Antigravity today",
"Using Grok today"), so grok and antigravity work in any repository with no
hand-written config. A repository's own [agents.<name>] always wins."""
from __future__ import annotations

import json

# Headless grok ends its whole turn as "cancelled" on any command outside its
# allow list (measured on grok 1.0.41), instead of refusing that one command,
# so it is told the limits up front. The relay also resumes a cancelled turn.
GROK_RULES = (
    "Headless run: nobody can approve a prompt, and any shell command outside "
    "your allow list ends your whole turn. Run one plain command per shell "
    "call. Run git as plain `git diff`, `git log`, `git show` or `git status`, "
    "never with `git -c …`, `git --no-pager` or `git -C <dir>`. Never use "
    "shell control flow (if, for, while, case), subshells, "
    "$(...), backticks, background &, or bash -c. Chain only with && or |, "
    "each part an allowed command. Prefer your read, search and edit tools to "
    "shell commands."
)

# Headless claude gets the same limits, plus the one test command it may run.
# adapters.claude.system_prompt fills in the command, or drops that line when
# the repository has none.
CLAUDE_RULES = GROK_RULES
CLAUDE_TEST_RULE = "Run tests only with `{test_command}`."

# Agents still reach for the no-colour forms of the read-only git commands, so
# exactly these three are allowed. Never `git -c` in general or
# `git --no-pager`: `-c` can set core.pager, core.sshCommand or alias.*, which
# make git run other programs.
NO_COLOUR_GIT = tuple(
    f"git -c color.ui=false -c color.diff=false {sub}"
    for sub in ("diff", "log", "show")
)

# agy 1.4.12's `--settings` applies to that run only, and its permissions.allow
# replaces the global file's list. Command rules match by prefix, and a chained
# command is refused unless each part is allowed. The commands are exactly the
# ones grok's recipe allows below: no `command(*)`, no pushing, no rm.
ANTIGRAVITY_ALLOW = (
    "read_file(*)", "write_file(*)", "edit_file(*)",
    "command(git add)", "command(git commit)", "command(git diff)",
    "command(git status)", "command(git log)", "command(git show)",
    "command(git rev-parse)", "command(whyline)", "command(python3)",
    "command(uv run)", "command(uv)", "command(mkdir)", "command(ls)",
    "command(find)", "command(touch)", "command(cat)", "command(grep)",
    "command(head)", "command(tail)", "command(wc)", "command(diff)",
    "command(pwd)", "command(pytest)", "command(.venv/bin/pytest)",
    "command(.venv/bin/python)",
    *(f"command({form})" for form in NO_COLOUR_GIT),
)

# `-p` must stay last: the relay appends the prompt as the final argument,
# and both CLIs' `-p` swallows whatever token follows it.
RECIPES: dict[str, tuple[str, ...]] = {
    "antigravity": (
        "agy", "--output-format", "json", "--mode", "accept-edits",
        "--add-dir", ".", "--new-project",
        "--settings", json.dumps({"permissions": {"allow": list(ANTIGRAVITY_ALLOW)}}),
        "-p",
    ),
    "grok": (
        "grok", "--output-format", "json", "--permission-mode", "dontAsk",
        "--deny", "Bash(git push:*)", "--deny", "Bash(rm -rf:*)",
        "--allow", "Edit", "--allow", "Bash(git add:*)", "--allow", "Bash(git commit:*)",
        "--allow", "Bash(git diff:*)", "--allow", "Bash(git status:*)",
        "--allow", "Bash(git log:*)", "--allow", "Bash(whyline:*)",
        "--allow", "Bash(python3:*)", "--allow", "Bash(uv run:*)", "--allow", "Bash(uv:*)",
        "--allow", "Bash(mkdir:*)", "--allow", "Bash(ls:*)", "--allow", "Bash(find:*)",
        "--allow", "Bash(touch:*)", "--allow", "Bash(cat:*)",
        "--allow", "Bash(grep:*)", "--allow", "Bash(head:*)", "--allow", "Bash(tail:*)",
        "--allow", "Bash(wc:*)", "--allow", "Bash(diff:*)", "--allow", "Bash(pwd:*)",
        "--allow", "Bash(git show:*)", "--allow", "Bash(git rev-parse:*)",
        "--allow", "Bash(pytest:*)", "--allow", "Bash(.venv/bin/pytest:*)",
        "--allow", "Bash(.venv/bin/python:*)", "--allow", "WebFetch",
        *(arg for form in NO_COLOUR_GIT for arg in ("--allow", f"Bash({form}:*)")),
        "--rules", GROK_RULES,
        "-p",
    ),
}

# A recipe is skipped when the config already names the agent under one of
# these older keys (chat.AGENT_ALIASES maps agy -> antigravity).
ALIASES: dict[str, tuple[str, ...]] = {"antigravity": ("agy",)}
