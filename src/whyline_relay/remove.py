"""Remove whyline-relay's repository-local setup."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from whyline_relay import config, gitcheck, running


def _files_under(root: Path, relay: Path) -> set[str]:
    """List file-like entries below relay without following symlinks."""
    relative_relay = relay.relative_to(root).as_posix()
    if relay.is_symlink():
        return {relative_relay}
    if not relay.exists():
        return set()
    if not relay.is_dir():
        return {relative_relay}

    found: set[str] = set()
    for current, directories, filenames in os.walk(relay, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            path = current_path / name
            if path.is_symlink():
                found.add(path.relative_to(root).as_posix())
                directories.remove(name)
        for name in filenames:
            found.add((current_path / name).relative_to(root).as_posix())
    return found


def _safe_relay_path(root: Path) -> Path | None:
    """Return the relay path unless reaching it traverses outside root."""
    resolved_root = root.resolve()
    relay = config.relay_dir(resolved_root)
    try:
        relay.parent.resolve().relative_to(resolved_root)
    except ValueError:
        return None
    return relay


def run(root: Path, *, assume_yes: bool, force: bool, confirm=input) -> int:
    root = root.resolve()
    relay = _safe_relay_path(root)
    if relay is None:
        print(
            "Refusing to remove .whyline/relay: its parent resolves outside "
            "the repository.",
            file=sys.stderr,
        )
        return 1

    active = running.live(root)
    if active is not None:
        print(
            f"Refusing to remove: another relay is running here (pid {active.pid}). "
            "Run `whyline-relay stop` first.",
            file=sys.stderr,
        )
        return 1

    relay_exists = relay.exists() or relay.is_symlink()
    exclude_count = gitcheck.relay_ignore_count(root)
    if not relay_exists and exclude_count == 0:
        print("Nothing to remove: whyline-relay is not set up here.")
        return 0

    relay_is_directory = not relay.is_symlink() and relay.is_dir()
    state_path = relay / "state.json"
    if relay_is_directory and state_path.exists() and not force:
        print(
            "Refusing to remove a paused or interrupted relay run. "
            "Run `whyline-relay resume`, or pass `--force` to remove it.",
            file=sys.stderr,
        )
        return 1

    files = _files_under(root, relay)
    tracked = files.intersection(gitcheck.tracked_paths(root, ".whyline/relay"))
    print(f"Files to remove: {len(files)} ({len(tracked)} tracked by git).")
    if relay_exists:
        if relay_is_directory or relay.is_symlink():
            print("Directory to remove: .whyline/relay/")
        else:
            print("File to remove: .whyline/relay")
    if exclude_count:
        print(
            f"Local Git exclude lines to remove: {exclude_count} "
            f"from .git/info/exclude."
        )

    if not assume_yes:
        try:
            answer = confirm("Remove these? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if not answer.startswith("y"):
            print("Nothing removed.")
            return 1

    if relay_exists:
        if relay_is_directory:
            shutil.rmtree(relay)
        else:
            relay.unlink()
    gitcheck.remove_relay_ignored(root)

    print("Removed whyline-relay from this repository.")
    if tracked:
        print(
            f"Deleted {len(tracked)} file(s) tracked by git. The deletions will "
            "show in `git status` and need committing."
        )
    return 0
