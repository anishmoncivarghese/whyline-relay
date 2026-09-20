from pathlib import Path

from whyline_relay import cli


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / ".whyline").mkdir(parents=True)
    (tmp_path / "plan.md").write_text(
        "- [x] WL-0: Done already\n- [ ] WL-1: Add the cache\n      Evict on write.\n"
    )
    return tmp_path


def test_dry_run_prints_the_next_task_and_argv(tmp_path: Path, capsys, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(cli.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")
    code = cli.main(["start", "--repo", str(repo), "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "WL-1" in out
    assert "WL-0" not in out
    assert "codex exec -s workspace-write" in out
    assert "PACKET" in out
    assert "Evict on write." in out


def test_dry_run_launches_nothing(tmp_path: Path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(cli.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")

    def explode(*args, **kwargs):
        raise AssertionError("dry run must not launch an agent")

    monkeypatch.setattr(cli.agents, "run", explode)
    assert cli.main(["start", "--repo", str(repo), "--dry-run"]) == 0


def test_dry_run_reports_an_empty_plan(tmp_path: Path, capsys):
    (tmp_path / ".whyline").mkdir(parents=True)
    (tmp_path / "plan.md").write_text("- [x] WL-1: Done\n")
    code = cli.main(["start", "--repo", str(tmp_path), "--dry-run"])
    assert code == 0
    assert "no unchecked tasks" in capsys.readouterr().out.lower()
