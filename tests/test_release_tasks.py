import json
from pathlib import Path
import subprocess
import sys

import pytest

from whyline_relay import cli, gitcheck, loop, plan, preflight, state

FAKE = str(Path(__file__).parent / "fake_pipeline_agent.py")

PLAN = (
    "- [ ] T-1: build\n"
    "  do it\n"
    "- [ ] T-2: release 1.0\n"
    "  relay-profile: release\n"
    "  Bump the version.\n"
    "  Tag v1.0 and push.\n"
    "- [ ] T-3: after\n"
    "  x\n"
)


def _configure_fake_agents(root: Path) -> None:
    cfg = root / ".whyline/relay/config.toml"
    cfg.write_text(
        '[roles]\n'
        'implementer = "codex"\n'
        'reviewer = "claude"\n'
        '[agents.codex]\n'
        f'command = ["{sys.executable}", "{FAKE}", "claude:ready-for-review", "{root}"]\n'
        '[agents.claude]\n'
        f'command = ["{sys.executable}", "{FAKE}", "commit:claude:approved", "{root}"]\n'
    )


def test_a_release_task_pauses_for_the_human_without_a_pipeline_profile(two_role_repo, monkeypatch):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    with pytest.raises(loop.Paused) as paused:
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)
    assert paused.value.reason.startswith("release task for you: T-2")
    assert state.load(root).task_id == "T-2"


def test_done_ticks_commits_and_moves_on(two_role_repo):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)
    loop.mark_done(root, "T-2")
    tasks = plan.parse((root / "plan.md").read_text())
    assert plan.find(tasks, "T-2").checked and state.load(root) is None


def test_done_for_the_wrong_task_is_refused(two_role_repo):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)
    with pytest.raises(ValueError, match="T-2"):
        loop.mark_done(root, "T-3")


def test_skip_leaves_it_unticked(two_role_repo):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)
    loop.mark_skipped(root, "T-2")
    assert not plan.find(plan.parse((root / "plan.md").read_text()), "T-2").checked


def test_skipped_tasks_stay_skipped_on_later_runs(two_role_repo):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)
    loop.mark_skipped(root, "T-2")

    skipped_file = root / ".whyline/relay/skipped-tasks.json"
    assert skipped_file.exists()
    assert json.loads(skipped_file.read_text()) == ["T-2"]
    assert ".whyline/relay/skipped-tasks.json" in gitcheck.RELAY_IGNORE

    _configure_fake_agents(root)
    outcomes = loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)
    assert [o.task_id for o in outcomes] == ["T-3"]
    tasks = plan.parse((root / "plan.md").read_text())
    assert not plan.find(tasks, "T-2").checked
    assert plan.find(tasks, "T-3").checked


def test_cli_resume_on_release_pause_prints_hint_and_returns_1(two_role_repo, capsys):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)

    code = cli.main(["resume", "--repo", str(root)])
    assert code == 1
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "whyline relay done T-2" in combined
    assert "whyline relay skip T-2" in combined


def test_cli_done_marks_done_and_continues(two_role_repo, capsys):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)

    _configure_fake_agents(root)
    code = cli.main(["done", "T-2", "--repo", str(root)])
    assert code == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "Marked T-2 done." in captured.out
    tasks = plan.parse((root / "plan.md").read_text())
    assert plan.find(tasks, "T-2").checked
    assert plan.find(tasks, "T-3").checked


def test_cli_skip_marks_skipped_and_continues(two_role_repo, capsys):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)

    _configure_fake_agents(root)
    code = cli.main(["skip", "T-2", "--repo", str(root)])
    assert code == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "Skipped T-2." in captured.out
    tasks = plan.parse((root / "plan.md").read_text())
    assert not plan.find(tasks, "T-2").checked
    assert plan.find(tasks, "T-3").checked


def test_release_agent_runs_with_replaced_implementer_pipeline(two_role_repo):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    cfg = root / ".whyline/relay/config.toml"
    cfg.write_text(
        '[roles]\n'
        'implementer = "codex"\n'
        'reviewer = "claude"\n'
        'release = "grok"\n'
        '[pipeline]\n'
        'default_profile = "full"\n'
        '[pipeline.profiles]\n'
        'full = ["draft", "review"]\n'
        '[pipeline.stages.draft]\n'
        'role = "implementer"\n'
        'prompt = "implement"\n'
        '[pipeline.stages.draft.on]\n'
        'ready = "@next"\n'
        '[pipeline.stages.review]\n'
        'role = "reviewer"\n'
        'prompt = "review"\n'
        '[pipeline.stages.review.on]\n'
        'approved = "@complete"\n'
        '[agents.codex]\n'
        f'command = ["{sys.executable}", "{FAKE}", "write:claude:ready", "{root}"]\n'
        '[agents.grok]\n'
        'adapter = "generic"\n'
        f'command = ["{sys.executable}", "{FAKE}", "write:claude:ready", "{root}"]\n'
        '[agents.claude]\n'
        f'command = ["{sys.executable}", "{FAKE}", "claude:approved", "{root}"]\n'
    )
    settings = loop.config.load(root)
    assert settings.pipeline is not None
    # Releases by grok under pipeline: T-2 runs without pausing, using grok
    outcomes = loop.run_plan(root, settings, root / "plan.md", branch="relay/plan", echo=False)
    assert [o.task_id for o in outcomes] == ["T-2", "T-3"]
    tasks = plan.parse((root / "plan.md").read_text())
    assert plan.find(tasks, "T-2").checked
    assert plan.find(tasks, "T-3").checked
    assert (root / ".whyline/relay/logs/T-2-1-grok-draft.log").exists()
    assert (root / ".whyline/relay/logs/T-3-1-codex-draft.log").exists()


def test_release_agent_runs_with_replaced_implementer_two_role(two_role_repo):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    cfg = root / ".whyline/relay/config.toml"
    cfg.write_text(
        '[roles]\n'
        'implementer = "codex"\n'
        'reviewer = "claude"\n'
        'release = "grok"\n'
        '[agents.codex]\n'
        f'command = ["{sys.executable}", "{FAKE}", "claude:ready-for-review", "{root}"]\n'
        '[agents.grok]\n'
        'adapter = "generic"\n'
        f'command = ["{sys.executable}", "{FAKE}", "claude:ready-for-review", "{root}"]\n'
        '[agents.claude]\n'
        f'command = ["{sys.executable}", "{FAKE}", "commit:claude:approved", "{root}"]\n'
    )
    settings = loop.config.load(root)
    # Releases by grok: T-2 runs without pausing, using grok
    outcomes = loop.run_plan(root, settings, root / "plan.md", branch="relay/plan", echo=False)
    assert [o.task_id for o in outcomes] == ["T-2", "T-3"]


def test_preflight_warns_for_codex_release_agent_with_workspace_write(tmp_path):
    relay = tmp_path / ".whyline/relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        '[roles]\n'
        'implementer = "claude"\n'
        'reviewer = "claude"\n'
        'release = "codex"\n'
        '[agents.codex]\n'
        'command = ["codex", "exec", "-s", "workspace-write"]\n'
    )
    (tmp_path / "plan.md").write_text("- [ ] T-1: do it\n")
    checks = preflight.run(tmp_path, tmp_path / "plan.md", allow_dirty=True)
    warnings = [c.message for c in checks if c.status == "warn"]
    assert any(
        "release tasks by codex: its workspace-write sandbox blocks network access and tag writes; release tasks will likely fail" in w
        for w in warnings
    )


def test_preflight_warns_for_other_release_agent(tmp_path):
    relay = tmp_path / ".whyline/relay"
    relay.mkdir(parents=True)
    (relay / "config.toml").write_text(
        '[roles]\n'
        'implementer = "codex"\n'
        'reviewer = "codex"\n'
        'release = "claude"\n'
    )
    (tmp_path / "plan.md").write_text("- [ ] T-1: do it\n")
    checks = preflight.run(tmp_path, tmp_path / "plan.md", allow_dirty=True)
    warnings = [c.message for c in checks if c.status == "warn"]
    assert any(
        "release tasks by claude: can't verify that it can push and tag; the first release task will show" in w
        for w in warnings
    )


def test_cli_done_switches_to_saved_branch_before_committing(two_role_repo, capsys):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)

    # Switch away from relay branch to main before calling CLI done
    subprocess.run(["git", "checkout", "main"], cwd=root, check=True, capture_output=True)
    main_commit_before = gitcheck.head_commit(root)
    assert gitcheck.current_branch(root) == "main"

    code = cli.main(["done", "T-2", "--repo", str(root)])
    assert code == cli.EXIT_OK

    # main branch should remain untouched
    main_commit_after = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert main_commit_after == main_commit_before

    # The current branch is relay/plan and contains the done commit
    assert gitcheck.current_branch(root) == "relay/plan"
    tasks = plan.parse((root / "plan.md").read_text())
    assert plan.find(tasks, "T-2").checked
    assert plan.find(tasks, "T-3").checked

    log = subprocess.run(
        ["git", "log", "-n", "3", "--oneline"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    assert "chore: release task T-2 done by hand" in log


def test_cli_done_after_only_does_not_run_subsequent_tasks(two_role_repo, capsys):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    code = cli.main(["start", "--only", "T-2", "--repo", str(root)])
    assert code == cli.EXIT_PAUSED
    assert state.load(root).only == "T-2"

    code = cli.main(["done", "T-2", "--repo", str(root)])
    assert code == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "Marked T-2 done." in captured.out
    assert "0 task(s) approved and committed" in captured.out

    tasks = plan.parse((root / "plan.md").read_text())
    assert plan.find(tasks, "T-2").checked
    assert not plan.find(tasks, "T-3").checked
    assert not (root / "feature-T-3.txt").exists()


def test_cli_skip_after_only_does_not_run_subsequent_tasks(two_role_repo, capsys):
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    code = cli.main(["start", "--only", "T-2", "--repo", str(root)])
    assert code == cli.EXIT_PAUSED
    assert state.load(root).only == "T-2"

    code = cli.main(["skip", "T-2", "--repo", str(root)])
    assert code == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "Skipped T-2." in captured.out
    assert "0 task(s) approved and committed" in captured.out

    tasks = plan.parse((root / "plan.md").read_text())
    assert not plan.find(tasks, "T-2").checked
    assert not plan.find(tasks, "T-3").checked
    assert not (root / "feature-T-3.txt").exists()
    assert json.loads((root / ".whyline/relay/skipped-tasks.json").read_text()) == ["T-2"]


def test_human_first_pause_plus_skip_leaves_git_clean_with_real_preflight(two_role_repo, monkeypatch):
    # Restore actual preflight checks rather than relying on autouse mock
    monkeypatch.setattr(cli, "_launch_checks", preflight.run)

    human_first_plan = (
        "- [ ] T-1: release 1.0\n"
        "  relay-profile: release\n"
        "  Tag and deploy.\n"
        "- [ ] T-2: subsequent\n"
        "  x\n"
    )
    root = two_role_repo(human_first_plan)
    # Initialize whyline so real preflight's whyline sync check passes
    subprocess.run(["whyline", "init", "--yes"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init whyline"], cwd=root, check=True, capture_output=True)

    # Start relay; pauses on human release T-1
    code = cli.main(["start", "--repo", str(root)])
    assert code == cli.EXIT_PAUSED
    assert not gitcheck.is_dirty(root)
    status_pause = subprocess.run(["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True).stdout
    assert status_pause == ""

    # Skip T-1; should pass real preflight and execute T-2
    code = cli.main(["skip", "T-1", "--repo", str(root)])
    assert code == cli.EXIT_OK
    assert not gitcheck.is_dirty(root)
    status_skip = subprocess.run(["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True).stdout
    assert status_skip == ""

    tasks = plan.parse((root / "plan.md").read_text())
    assert not plan.find(tasks, "T-1").checked
    assert plan.find(tasks, "T-2").checked


def test_preflight_allows_release_profile_under_pipeline(tmp_path):
    root = tmp_path
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("x\n")
    (root / "plan.md").write_text(
        "- [ ] T-1: build\n"
        "  do it\n"
        "- [ ] T-2: release 1.0\n"
        "  relay-profile: release\n"
        "  Bump version.\n"
    )
    relay = root / ".whyline" / "relay"
    relay.mkdir(parents=True, exist_ok=True)
    (relay / "config.toml").write_text(
        '[roles]\n'
        'implementer = "codex"\n'
        'reviewer = "claude"\n'
        'release = "grok"\n'
        '[pipeline]\n'
        'default_profile = "full"\n'
        '[pipeline.profiles]\n'
        'full = ["draft", "review"]\n'
        '[pipeline.stages.draft]\n'
        'role = "implementer"\n'
        'prompt = "implement"\n'
        '[pipeline.stages.draft.on]\n'
        'ready = "@next"\n'
        '[pipeline.stages.review]\n'
        'role = "reviewer"\n'
        'prompt = "review"\n'
        '[pipeline.stages.review.on]\n'
        'approved = "@complete"\n'
        '[agents.codex]\n'
        f'command = ["{sys.executable}", "codex"]\n'
        '[agents.claude]\n'
        f'command = ["{sys.executable}", "claude"]\n'
        '[agents.grok]\n'
        'adapter = "generic"\n'
        f'command = ["{sys.executable}", "grok"]\n'
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "setup"], cwd=root, check=True, capture_output=True)
    subprocess.run(["whyline", "init", "--yes"], cwd=root, check=True, capture_output=True)

    checks = preflight.run(root, root / "plan.md", allow_dirty=True)
    # Must NOT fail on relay-profile: release
    profile_failures = [c for c in checks if c.status == "FAIL" and "relay-profile" in c.message]
    assert not profile_failures


def test_cli_done_on_final_task_finishes_successfully(two_role_repo, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_launch_checks", preflight.run)
    final_task_plan = (
        "- [x] T-1: build\n"
        "  do it\n"
        "- [ ] T-2: release 1.0\n"
        "  relay-profile: release\n"
        "  Bump version and tag.\n"
    )
    root = two_role_repo(final_task_plan)
    subprocess.run(["whyline", "init", "--yes"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init whyline"], cwd=root, check=True, capture_output=True)

    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)

    code = cli.main(["done", "T-2", "--repo", str(root)])
    assert code == cli.EXIT_OK
    captured = capsys.readouterr()
    assert "Marked T-2 done." in captured.out
    assert "Plan complete: 0 task(s) approved and committed." in captured.out

    tasks = plan.parse((root / "plan.md").read_text())
    assert plan.find(tasks, "T-2").checked
    assert state.load(root) is None


def test_cli_skip_switches_to_saved_branch_before_running(two_role_repo, monkeypatch):
    monkeypatch.setattr(cli, "_launch_checks", preflight.run)
    root = two_role_repo(PLAN.replace("- [ ] T-1", "- [x] T-1"))
    subprocess.run(["whyline", "init", "--yes"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init whyline"], cwd=root, check=True, capture_output=True)

    with pytest.raises(loop.Paused):
        loop.run_plan(root, loop.config.load(root), root / "plan.md", branch="relay/plan", echo=False)

    # Switch away from relay branch to main before calling CLI skip
    subprocess.run(["git", "checkout", "main"], cwd=root, check=True, capture_output=True)
    main_commit_before = gitcheck.head_commit(root)
    assert gitcheck.current_branch(root) == "main"

    _configure_fake_agents(root)
    code = cli.main(["skip", "T-2", "--repo", str(root)])
    assert code == cli.EXIT_OK

    # main branch should remain untouched
    main_commit_after = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert main_commit_after == main_commit_before

    # The current branch is relay/plan and contains the T-3 commit
    assert gitcheck.current_branch(root) == "relay/plan"
    tasks = plan.parse((root / "plan.md").read_text())
    assert not plan.find(tasks, "T-2").checked
    assert plan.find(tasks, "T-3").checked

