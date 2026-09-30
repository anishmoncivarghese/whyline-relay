import subprocess
from pathlib import Path

import pytest

from whyline_relay import config, setup


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


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


def test_fresh_repo_gets_the_default_pipeline(repo):
    paths = setup.write_roles(repo, "codex", "claude", "claude")
    assert paths == setup.role_paths(repo)
    loaded = config.load(repo)
    assert set(loaded.pipeline.stages) == {"draft", "test", "review"}
    assert loaded.backup_chain == []


def test_commit_contains_only_the_setup_files(repo):
    (repo / "unrelated.txt").write_text("not mine\n")
    setup.write_roles(repo, "codex", "claude", "claude")
    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == [
        ".whyline/relay/config.toml",
        ".whyline/relay/prompts/test.md",
    ]
    assert "unrelated.txt" in _git(repo, "status", "--porcelain")


def test_backup_chain_is_written_and_removed(repo):
    setup.write_roles(repo, "codex", "claude", "claude", backup=["claude"])
    assert config.load(repo).backup_chain == ["claude"]
    setup.write_roles(repo, "codex", "claude", "claude", backup=[])
    assert "[backup]" not in _config(repo).read_text()
    assert config.load(repo).backup_chain == []


def test_existing_custom_pipeline_keeps_everything_but_the_roles(repo):
    setup.write_roles(repo, "codex", "claude", "claude")
    custom = (
        _config(repo)
        .read_text()
        .replace("[roles]", '# my notes stay\n[roles]\ndesigner = "claude"')
        .replace("max_visits = 5", "max_visits = 9")
    )
    _config(repo).write_text(custom)
    setup.write_roles(repo, "claude", "codex", "codex", backup=["codex"])
    text = _config(repo).read_text()
    assert "# my notes stay" in text
    assert 'designer = "claude"' in text
    assert "max_visits = 9" in text
    assert 'implementer = "claude"' in text
    assert 'tester      = "codex"' in text
    assert 'reviewer    = "codex"' in text
    assert config.load(repo).backup_chain == ["codex"]


def test_old_two_role_config_is_replaced_by_the_pipeline_template(repo):
    _config(repo).parent.mkdir(parents=True)
    _config(repo).write_text('[roles]\nimplementer = "codex"\nreviewer = "claude"\n')
    setup.write_roles(repo, "codex", "claude", "claude")
    assert config.load(repo).pipeline is not None


def test_write_roles_can_skip_the_commit(tmp_path):
    # Not a git repo at all: the terminal wizard's own tests run like this.
    setup.write_roles(tmp_path, "codex", "claude", "claude", commit=False)
    assert _config(tmp_path).exists()
