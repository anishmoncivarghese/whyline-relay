"""Default commands for the agents the relay runs through the generic
adapter. They are the README's verified recipes ("Using Antigravity today",
"Using Grok today"), so grok and antigravity work in any repository with no
hand-written config. A repository's own [agents.<name>] always wins."""
from __future__ import annotations

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
        "-p",
    ),
}

# A recipe is skipped when the config already names the agent under one of
# these older keys (chat.AGENT_ALIASES maps agy -> antigravity).
ALIASES: dict[str, tuple[str, ...]] = {"antigravity": ("agy",)}
