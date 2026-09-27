import subprocess
from pathlib import Path

from whyline_relay import brainstorm, config


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def test_temp_path_is_per_agent_under_the_gitignored_directory(tmp_path: Path):
    path = brainstorm.temp_path(tmp_path, "claude")
    assert path == tmp_path / ".whyline" / "relay" / "brainstorm-tmp" / "claude.md"


def test_shared_path_uses_the_slugified_topic(tmp_path: Path):
    path = brainstorm.shared_path(tmp_path, "Best Caching Strategy!")
    assert path == tmp_path / "docs" / "brainstorm" / "best-caching-strategy.md"


def test_run_pass_zero_writes_a_temp_file_per_model(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        # Simulate the agent editing its own temp file, the way a real
        # agent's own tool use would -- run_fn itself never writes files in
        # production; this fake stands in for that.
        for agent in ("claude", "codex"):
            if agent in command[0]:
                brainstorm.temp_path(tmp_path, agent).parent.mkdir(
                    parents=True, exist_ok=True
                )
                brainstorm.temp_path(tmp_path, agent).write_text(
                    f"{agent} findings\n"
                )
        return RunResult(0, '{"type":"result","result":"done"}\n')

    brainstorm.run_pass_zero(
        tmp_path, models, "my topic", settings=settings, run_fn=fake_run_fn,
        print_fn=lambda *a, **k: None,
    )
    assert brainstorm.temp_path(tmp_path, "claude").read_text() == "claude findings\n"
    assert brainstorm.temp_path(tmp_path, "codex").read_text() == "codex findings\n"


def test_run_pass_zero_skips_a_model_that_is_missing(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude")]

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay import agents
        raise agents.AgentMissing("claude is not installed")

    printed = []
    brainstorm.run_pass_zero(
        tmp_path, models, "my topic", settings=settings, run_fn=fake_run_fn,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    assert not brainstorm.temp_path(tmp_path, "claude").exists()
    assert any("claude" in line for line in printed)


def test_merge_pass_zero_combines_temp_files_into_the_shared_file(tmp_path: Path):
    _init_repo(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    brainstorm.temp_path(tmp_path, "claude").parent.mkdir(parents=True, exist_ok=True)
    brainstorm.temp_path(tmp_path, "claude").write_text("claude findings\n")
    brainstorm.temp_path(tmp_path, "codex").write_text("codex findings\n")
    brainstorm.merge_pass_zero(tmp_path, models, "my topic")
    shared = brainstorm.shared_path(tmp_path, "my topic").read_text()
    assert "## Claude" in shared
    assert "claude findings" in shared
    assert "## Codex" in shared
    assert "codex findings" in shared
    assert shared.index("## Claude") < shared.index("## Codex")
    assert not brainstorm.temp_path(tmp_path, "claude").exists()
    assert not brainstorm.temp_path(tmp_path, "codex").exists()
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=tmp_path,
        check=True, capture_output=True, text=True,
    ).stdout
    assert 'brainstorm: merge independent research on "my topic"' in log


def test_merge_pass_zero_skips_an_empty_or_missing_temp_file(tmp_path: Path):
    _init_repo(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    brainstorm.temp_path(tmp_path, "claude").parent.mkdir(parents=True, exist_ok=True)
    brainstorm.temp_path(tmp_path, "claude").write_text("claude findings\n")
    # codex's temp file was never created (e.g. it was skipped in pass 0)
    brainstorm.merge_pass_zero(tmp_path, models, "my topic")
    shared = brainstorm.shared_path(tmp_path, "my topic").read_text()
    assert "## Claude" in shared
    assert "## Codex" not in shared
