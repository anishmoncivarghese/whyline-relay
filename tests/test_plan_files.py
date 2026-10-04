import subprocess
from pathlib import Path

import pytest

from whyline_relay import config, planner, setup


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def _config(root: Path) -> Path:
    return root / ".whyline" / "relay" / "config.toml"


def test_approve_to_a_named_file_commits_only_it(repo):
    draft = repo / "d.md"
    draft.write_text("<!-- whyline-plan v1 | source: paste -->\n- [ ] T-1: x\n  y.\n")
    target = repo / "plans" / "first.plan.md"
    result = planner.approve(repo, config.load(repo), draft, drafted_by="hand", target=target)
    assert result == target and target.read_text() == draft.read_text()
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["plans/first.plan.md"]
    assert _git(repo, "log", "-1", "--format=%s").strip() == (
        "docs: add plan plans/first.plan.md drafted by hand"
    )
    assert not (repo / "plan.md").exists()


def test_approve_without_target_still_writes_plan_md(repo):
    draft = repo / "d.md"
    draft.write_text("- [ ] T-1: x\n")
    assert planner.approve(repo, config.load(repo), draft, drafted_by="codex") == repo / "plan.md"
    assert _git(repo, "log", "-1", "--format=%s").strip() == "docs: add plan drafted by codex"


def test_write_plan_sets_only_the_top_level_key(repo):
    _config(repo).parent.mkdir(parents=True)
    _config(repo).write_text('# mine\nmax_rounds = 4\n\n[roles]\nimplementer = "codex"\n')
    setup.write_plan(repo, "plans/first.plan.md")
    text = _config(repo).read_text()
    assert text.startswith('# mine\nmax_rounds = 4\nplan = "plans/first.plan.md"\n')
    assert config.load(repo).plan == "plans/first.plan.md"
    setup.write_plan(repo, "plans/second.plan.md")
    assert config.load(repo).plan == "plans/second.plan.md"
    assert _config(repo).read_text().count("plan =") == 1


def test_write_plan_creates_a_missing_config(repo):
    setup.write_plan(repo, "plans/a.plan.md")
    assert config.load(repo).plan == "plans/a.plan.md"
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").split() == [
        ".whyline/relay/config.toml"
    ]


def test_write_planner_sets_draft_and_review(repo):
    setup.write_planner(repo, "claude", "codex")
    loaded = config.load(repo)
    assert (loaded.planner.draft, loaded.planner.review) == ("claude", "codex")


def test_write_roles_on_an_old_config_keeps_every_other_setting(repo):
    _config(repo).parent.mkdir(parents=True)
    _config(repo).write_text(
        '# mine\nplan = "plan.md"\nmax_rounds = 6\ntimeout_minutes = 45\n'
        'branch_prefix = "work/"\n\n'
        '[roles]\nimplementer = "antigravity"\nreviewer = "codex"\n\n'
        '[status_map]\nready-for-review = "review"\n\n'
        '[agents.grok]\nadapter = "generic"\ncommand = [\n  "grok", "--mine",\n  "-p"\n]\n\n'
        '[planner]\ndraft = "claude"\n\n'
        '[backup]\nchain = ["claude"]\n'
    )
    setup.write_roles(repo, "antigravity", "claude", "codex", ["grok"])
    text = _config(repo).read_text()
    loaded = config.load(repo)
    assert loaded.pipeline is not None
    assert (loaded.max_rounds, loaded.timeout_minutes, loaded.branch_prefix) == (6, 45, "work/")
    assert loaded.agents["grok"] == ["grok", "--mine", "-p"]
    assert loaded.planner.draft == "claude"
    assert loaded.backup_chain == ["grok"]
    assert "[status_map]" not in text and text.startswith("# mine\n")
    assert text.count("[roles]") == 1


def test_write_roles_keeps_plan_and_planner_written_before_setup(repo):
    setup.write_planner(repo, "grok", "claude")
    setup.write_plan(repo, "plans/a.plan.md")
    setup.write_roles(repo, "codex", "claude", "claude")
    loaded = config.load(repo)
    assert loaded.pipeline is not None
    assert loaded.plan == "plans/a.plan.md"
    assert (loaded.planner.draft, loaded.planner.review) == ("grok", "claude")
