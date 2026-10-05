"""Default commands for the agents the relay runs through the generic
adapter. They are the README's verified recipes ("Using Antigravity today",
"Using Grok today"), so grok and antigravity work in any repository with no
hand-written config. A repository's own [agents.<name>] always wins."""
from __future__ import annotations

# Headless grok ends its whole turn as "cancelled" on any command outside its
# allow list (measured on grok 1.0.41), instead of refusing that one command,
# so it is told the limits up front. The relay also resumes a cancelled turn.
GROK_RULES = (
    "Headless run: nobody can approve a prompt, and any shell command outside "
    "your allow list ends your whole turn. Run one plain command per shell "
    "call. Never use shell control flow (if, for, while, case), subshells, "
    "$(...), backticks, background &, or bash -c. Chain only with && or |, "
    "each part an allowed command. Prefer your read, search and edit tools to "
    "shell commands."
)

# `-p` must stay last: the relay appends the prompt as the final argument,
# and both CLIs' `-p` swallows whatever token follows it.
RECIPES: dict[str, tuple[str, ...]] = {
    "antigravity": (
        "agy", "--output-format", "json", "--mode", "accept-edits",
        "--add-dir", ".", "--new-project", "-p",
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
        "--rules", GROK_RULES,
        "-p",
    ),
}

# A recipe is skipped when the config already names the agent under one of
# these older keys (chat.AGENT_ALIASES maps agy -> antigravity).
ALIASES: dict[str, tuple[str, ...]] = {"antigravity": ("agy",)}
