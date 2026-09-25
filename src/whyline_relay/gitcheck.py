"""Git guards. Git is the authority; the relay only asks it questions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

RELAY_IGNORE = (
    ".whyline/relay/logs/",
    ".whyline/relay/state.json*",
    ".whyline/relay/plan-state.json*",
    ".whyline/relay/draft-plan.md",
    ".whyline/relay/STOP",
    ".whyline/relay/running.json",
    ".whyline/relay/active-roles.json",
)


class GitError(RuntimeError):
    """A git command failed or git is unavailable."""


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True
        )
    except FileNotFoundError as error:
        raise GitError("git is not installed or not on PATH") from error
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def current_branch(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD")


def is_dirty(root: Path) -> bool:
    return bool(_git(root, "status", "--porcelain"))


def head_commit(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD")


def commit_message(root: Path, commit: str) -> str:
    return _git(root, "log", "-1", "--format=%B", commit)


def ensure_branch(root: Path, name: str) -> None:
    """Switch to `name`, creating it from the current commit if it does not exist."""
    try:
        _git(root, "rev-parse", "--verify", f"refs/heads/{name}")
    except GitError:
        _git(root, "checkout", "-b", name)
        return
    _git(root, "checkout", name)


def _names_task(message: str, task_id: str) -> bool:
    """True when `task_id` appears as a whole id, not as the start of a longer one.

    A plain substring test would let a commit for RELAY-10 verify RELAY-1.
    """
    pattern = rf"(?<![\w-]){re.escape(task_id)}(?![\w-]|\.\w)"
    return re.search(pattern, message) is not None


def commit_verified(root: Path, base_commit: str, task_id: str) -> bool:
    """True when HEAD moved past `base_commit` and names `task_id` in its message.

    A ticked checkbox must always mean a real commit exists for that task, so
    both halves are required and neither is inferred from the agent's word.
    """
    head = head_commit(root)
    if head == base_commit:
        return False
    return _names_task(commit_message(root, head), task_id)


def ensure_relay_ignored(root: Path) -> None:
    """Keep the relay's runtime files out of `git status` and commits.

    Written to .git/info/exclude, which is local and never committed, so this
    neither dirties the tree nor adds a file to a task's commit. Without it the
    reviewer's `git add -A` sweeps the agent logs into the commit. Idempotent.
    """
    target = Path(_git(root, "rev-parse", "--git-path", "info/exclude"))
    if not target.is_absolute():
        target = root / target
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    present = existing.splitlines()
    missing = [pattern for pattern in RELAY_IGNORE if pattern not in present]
    if not missing:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write("\n".join(missing) + "\n")


def _relay_exclude_path(root: Path) -> Path:
    target = Path(_git(root, "rev-parse", "--git-path", "info/exclude"))
    return target if target.is_absolute() else root / target


def relay_ignore_count(root: Path) -> int:
    """Return the number of relay-owned lines in the local Git excludes."""
    target = _relay_exclude_path(root)
    if not target.exists():
        return 0
    lines = target.read_text(encoding="utf-8").splitlines()
    return sum(line in RELAY_IGNORE for line in lines)


def remove_relay_ignored(root: Path) -> int:
    """Remove only relay-owned entries from .git/info/exclude.

    Returns the number of removed lines. All other content, including its line
    endings, is preserved.
    """
    target = _relay_exclude_path(root)
    if not target.exists():
        return 0
    with target.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    kept: list[str] = []
    removed = 0
    for line in content.splitlines(keepends=True):
        if line.rstrip("\r\n") in RELAY_IGNORE:
            removed += 1
        else:
            kept.append(line)
    if removed:
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write("".join(kept))
    return removed


def tracked_paths(root: Path, pathspec: str) -> list[str]:
    """Return tracked paths matching a repository-relative pathspec."""
    output = _git(root, "ls-files", "-z", "--", pathspec)
    return [path for path in output.split("\0") if path]


def dirty_paths(root: Path) -> list[str]:
    """Paths `git status` reports as changed or untracked, ignored files excluded."""
    output = _git(root, "status", "--porcelain", "--untracked-files=all")
    return [line.split(maxsplit=1)[1] for line in output.splitlines() if line.strip()]


def commit_paths(root: Path, paths: list[Path], message: str) -> None:
    """Commit only `paths`, leaving everything else in the tree alone.

    Does nothing when those paths have no changes, so it is safe to repeat.
    """
    try:
        relative = [str(Path(p).resolve().relative_to(root.resolve())) for p in paths]
    except ValueError as error:
        raise GitError(f"{error}: a path lies outside the repository") from error
    _git(root, "add", "--", *relative)
    if not _git(root, "diff", "--cached", "--name-only", "--", *relative):
        return
    _git(root, "commit", "-m", message, "--", *relative)


def commit_all(root: Path, message: str) -> bool:
    """Stage and commit everything in the tree as one commit.

    For a configured pipeline (spec 5.6): the relay itself makes the one commit
    that finishes a task, instead of trusting an agent to. Returns False,
    committing nothing, when there is nothing staged -- a task that touched no
    files (for example, one that only recorded a decision) is not an error, and
    repeating this call is always safe.
    """
    _git(root, "add", "-A")
    if not _git(root, "diff", "--cached", "--name-only"):
        return False
    _git(root, "commit", "-m", message)
    return True
