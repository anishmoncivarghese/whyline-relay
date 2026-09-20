import subprocess
from pathlib import Path

import pytest

from whyline_relay import gitcheck


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "README.md").write_text("hello\n")
    git("add", "-A")
    git("commit", "-m", "initial commit")
    return tmp_path


def test_current_branch(repo: Path):
    assert gitcheck.current_branch(repo) == "main"


def test_clean_tree_is_not_dirty(repo: Path):
    assert gitcheck.is_dirty(repo) is False


def test_untracked_file_makes_it_dirty(repo: Path):
    (repo / "new.txt").write_text("x")
    assert gitcheck.is_dirty(repo) is True


def test_ensure_branch_creates_then_reuses(repo: Path):
    gitcheck.ensure_branch(repo, "relay/plan")
    assert gitcheck.current_branch(repo) == "relay/plan"
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    gitcheck.ensure_branch(repo, "relay/plan")
    assert gitcheck.current_branch(repo) == "relay/plan"


def test_commit_verified_requires_a_new_commit(repo: Path):
    base = gitcheck.head_commit(repo)
    assert gitcheck.commit_verified(repo, base, "WL-1") is False


def test_commit_verified_requires_the_task_id_in_the_message(repo: Path):
    base = gitcheck.head_commit(repo)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: something unrelated"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    assert gitcheck.commit_verified(repo, base, "WL-1") is False


def test_commit_verified_passes_when_both_hold(repo: Path):
    base = gitcheck.head_commit(repo)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add the cache (WL-1)"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    assert gitcheck.commit_verified(repo, base, "WL-1") is True


@pytest.mark.parametrize(
    ("message", "task_id", "expected"),
    [
        ("feat: add the cache (WL-10)", "WL-1", False),
        ("feat: add the cache (WL-1)", "WL-1", True),
        ("WL-1: add the cache", "WL-1", True),
        ("feat: add the cache for WL-2, WL-1", "WL-1", True),
        ("feat: add the cache (WL-0.2.0)", "WL-0.2", False),
        ("feat: add the cache (WL-0.2.0)", "WL-0.2.0", True),
        ("feat: add the cache (XWL-1)", "WL-1", False),
    ],
)
def test_commit_verified_matches_the_whole_task_id(
    repo: Path, message: str, task_id: str, expected: bool
):
    """A commit for WL-10 must not verify WL-1, or a ticked box could lie."""
    base = gitcheck.head_commit(repo)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True
    )
    assert gitcheck.commit_verified(repo, base, task_id) is expected


def test_relay_files_are_ignored_and_ignoring_is_idempotent(repo: Path):
    relay = repo / ".whyline" / "relay"
    (relay / "logs").mkdir(parents=True)
    (relay / "logs" / "WL-1-1-codex.log").write_text("x")
    (relay / "state.json").write_text("{}")
    (relay / "STOP").write_text("")
    (relay / "config.toml").write_text("max_rounds = 3\n")
    assert "logs" in _status(repo)
    gitcheck.ensure_relay_ignored(repo)
    gitcheck.ensure_relay_ignored(repo)
    status = _status(repo)
    assert "logs" not in status and "state.json" not in status and "STOP" not in status
    assert "config.toml" in status
    exclude = (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count(".whyline/relay/logs/") == 1


def _status(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


def test_dirty_paths_lists_modified_and_untracked_files(repo: Path):
    (repo / "README.md").write_text("changed\n")
    (repo / "new.txt").write_text("x")
    assert sorted(gitcheck.dirty_paths(repo)) == ["README.md", "new.txt"]


def test_commit_paths_commits_only_the_named_files(repo: Path):
    (repo / "README.md").write_text("changed\n")
    (repo / "other.txt").write_text("x")
    gitcheck.commit_paths(repo, [repo / "README.md"], "chore: tick WL-1 in the plan")
    assert gitcheck.dirty_paths(repo) == ["other.txt"]
    assert gitcheck.commit_message(repo, gitcheck.head_commit(repo)) == "chore: tick WL-1 in the plan"
    before = gitcheck.head_commit(repo)
    gitcheck.commit_paths(repo, [repo / "README.md"], "chore: again")
    assert gitcheck.head_commit(repo) == before
