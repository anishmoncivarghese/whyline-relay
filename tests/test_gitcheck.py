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
    (relay / "running.json").write_text("{}")
    (relay / "config.toml").write_text("max_rounds = 3\n")
    assert "logs" in _status(repo)
    gitcheck.ensure_relay_ignored(repo)
    gitcheck.ensure_relay_ignored(repo)
    status = _status(repo)
    assert all(
        name not in status
        for name in ("logs", "state.json", "STOP", "running.json")
    )
    assert "config.toml" in status
    exclude = (repo / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count(".whyline/relay/logs/") == 1
    assert exclude.count(".whyline/relay/running.json") == 1


_AUTO_AND_CHAT_IGNORES = (
    ".whyline/relay/auto-run.json",
    ".whyline/relay/auto-run.lock",
    ".whyline/relay/chat-active-agents.json",
    ".whyline/relay/recovery.json",
)


def test_relay_ignore_contains_auto_runtime_and_chat_failover_files():
    for pattern in _AUTO_AND_CHAT_IGNORES:
        assert pattern in gitcheck.RELAY_IGNORE


def test_ensure_relay_ignored_appends_new_runtime_files_without_duplicating(repo: Path):
    """An exclude file written before these paths existed must gain them once."""
    target = repo / ".git" / "info" / "exclude"
    old = [line for line in gitcheck.RELAY_IGNORE if line not in _AUTO_AND_CHAT_IGNORES]
    already = _AUTO_AND_CHAT_IGNORES[0]
    target.write_text("# kept\n" + "\n".join([*old, already]) + "\n")

    gitcheck.ensure_relay_ignored(repo)
    gitcheck.ensure_relay_ignored(repo)

    lines = target.read_text().splitlines()
    assert lines[0] == "# kept"
    for pattern in gitcheck.RELAY_IGNORE:
        assert lines.count(pattern) == 1


def test_existing_relay_excludes_pick_up_the_running_marker(repo: Path):
    target = repo / ".git" / "info" / "exclude"
    target.write_text("\n".join(gitcheck.RELAY_IGNORE[:-1]) + "\n")

    gitcheck.ensure_relay_ignored(repo)

    assert target.read_text().splitlines().count(".whyline/relay/running.json") == 1


def test_remove_relay_ignored_preserves_every_other_line(repo: Path):
    target = repo / ".git" / "info" / "exclude"
    target.write_text(
        "# local rules\r\n"
        ".whyline/relay/logs/\r\n"
        "dist/\r\n"
        ".whyline/relay/STOP"
        "\r\n.whyline/relay/running.json"
    )
    assert gitcheck.relay_ignore_count(repo) == 3
    assert gitcheck.remove_relay_ignored(repo) == 3
    assert target.read_bytes() == b"# local rules\r\ndist/\r\n"


def _status(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


def test_dirty_paths_lists_modified_and_untracked_files(repo: Path):
    (repo / "README.md").write_text("changed\n")
    (repo / "new.txt").write_text("x")
    assert sorted(gitcheck.dirty_paths(repo)) == ["README.md", "new.txt"]


def _track_decision_log(repo: Path) -> Path:
    log = repo / ".whyline" / "decisions.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("# Decisions\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "track the decision log"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return log


def _staged_names(repo: Path) -> str:
    return subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_commit_decision_log_commits_only_the_decision_log(repo: Path):
    log = _track_decision_log(repo)
    log.write_text("# Decisions\n\n- note\n")

    assert gitcheck.commit_decision_log(repo, "BAK-3") is True
    assert gitcheck.dirty_paths(repo) == []
    assert (
        gitcheck.commit_message(repo, gitcheck.head_commit(repo))
        == "chore: record BAK-3 decisions"
    )
    shown = subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert shown.strip() == ".whyline/decisions.md"


@pytest.mark.parametrize("extra", ["tracked", "untracked"])
def test_commit_decision_log_commits_nothing_when_another_path_is_dirty(
    repo: Path, extra: str
):
    log = _track_decision_log(repo)
    log.write_text("# Decisions\n\n- note\n")
    if extra == "tracked":
        (repo / "README.md").write_text("changed\n")
    else:
        (repo / "scratch.txt").write_text("x\n")
    before = gitcheck.head_commit(repo)

    assert gitcheck.commit_decision_log(repo, "BAK-3") is False

    assert gitcheck.head_commit(repo) == before
    assert _staged_names(repo) == ""
    dirty = gitcheck.dirty_paths(repo)
    assert ".whyline/decisions.md" in dirty
    assert len(dirty) == 2


def test_commit_decision_log_commits_nothing_on_a_clean_tree(repo: Path):
    before = gitcheck.head_commit(repo)

    assert gitcheck.commit_decision_log(repo, "BAK-3") is False

    assert gitcheck.head_commit(repo) == before
    assert gitcheck.dirty_paths(repo) == []
    assert _staged_names(repo) == ""


def test_commit_paths_commits_only_the_named_files(repo: Path):
    (repo / "README.md").write_text("changed\n")
    (repo / "other.txt").write_text("x")
    gitcheck.commit_paths(repo, [repo / "README.md"], "chore: tick WL-1 in the plan")
    assert gitcheck.dirty_paths(repo) == ["other.txt"]
    assert gitcheck.commit_message(repo, gitcheck.head_commit(repo)) == "chore: tick WL-1 in the plan"
    before = gitcheck.head_commit(repo)
    gitcheck.commit_paths(repo, [repo / "README.md"], "chore: again")
    assert gitcheck.head_commit(repo) == before


def test_commit_all_stages_and_commits_everything(repo: Path):
    (repo / "README.md").write_text("changed\n")
    (repo / "other.txt").write_text("x")
    committed = gitcheck.commit_all(repo, "feat: two files (T-1)")
    assert committed is True
    assert gitcheck.dirty_paths(repo) == []
    assert (
        gitcheck.commit_message(repo, gitcheck.head_commit(repo))
        == "feat: two files (T-1)"
    )


def test_commit_all_does_nothing_and_returns_false_when_the_tree_is_clean(
    repo: Path,
):
    before = gitcheck.head_commit(repo)
    committed = gitcheck.commit_all(repo, "chore: nothing")
    assert committed is False
    assert gitcheck.head_commit(repo) == before


def test_commit_stat_reports_a_new_file_in_the_latest_commit(repo: Path):
    (repo / "new.txt").write_text("made by an agent\n")
    gitcheck.commit_all(repo, "add new.txt")
    assert "new.txt" in gitcheck.commit_stat(repo)


def test_commit_stat_reports_a_change_to_a_tracked_file(repo: Path):
    (repo / "README.md").write_text("changed\n")
    gitcheck.commit_all(repo, "change readme")
    assert "README.md" in gitcheck.commit_stat(repo)
