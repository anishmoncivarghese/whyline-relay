import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from whyline_relay import cli, config, loop, plan

FAKE = str(Path(__file__).parent / "fake_agent.py")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-b", "relay/plan")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n")
    (tmp_path / ".whyline").mkdir()
    (tmp_path / ".whyline" / ".gitignore").write_text("active-handoff.json\nledger.jsonl\n")
    (tmp_path / "plan.md").write_text("- [ ] WL-1: One\n- [ ] WL-2: Two\n")
    git("add", "-A")
    git("commit", "-m", "initial")
    monkeypatch.setattr(loop.whylinecmd, "sync", lambda root, task, runner=None: "PACKET")
    monkeypatch.setattr(loop.whylinecmd, "claim", lambda *a, **k: None)
    return tmp_path


def approving_settings(root: Path, monkeypatch) -> config.Config:
    base = config.load(root)
    settings = config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, FAKE, "review", str(root)],
            "claude": [sys.executable, FAKE, "approve", str(root)],
        },
        status_map=base.status_map,
    )
    real_run = loop.agents.run
    counter = {"n": 0}

    def run_then_commit(command, prompt, **kwargs):
        code = real_run(command, prompt, **kwargs)
        if "approve" in command:
            counter["n"] += 1
            task_id = "WL-1" if counter["n"] == 1 else "WL-2"
            (root / f"f{counter['n']}.py").write_text("x = 1\n")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", f"feat: work ({task_id})"],
                cwd=root,
                check=True,
                capture_output=True,
            )
        return code

    monkeypatch.setattr(loop.agents, "run", run_then_commit)
    return settings


def test_runs_every_unchecked_task_and_ticks_each(repo: Path, monkeypatch):
    settings = approving_settings(repo, monkeypatch)
    outcomes = loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)
    assert [outcome.task_id for outcome in outcomes] == ["WL-1", "WL-2"]
    tasks = plan.parse((repo / "plan.md").read_text())
    assert all(task.checked for task in tasks)


def test_progress_lines_cover_a_full_approved_run_in_order(
    repo: Path, monkeypatch, capsys
):
    settings = approving_settings(repo, monkeypatch)
    loop.run_plan(
        repo, settings, repo / "plan.md", branch="relay/plan", only="WL-1"
    )
    output = capsys.readouterr().out
    statuses = [
        line for line in output.splitlines()
        if re.match(r"^\[\d{2}:\d{2}:\d{2}\] (?:==>|<==)", line)
    ]
    assert len(statuses) == 5
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\] ==> codex: implementing WL-1 \(round 1 of 3\)",
        statuses[0],
    )
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\] <== codex finished in \d+(?:m\d+)?s",
        statuses[1],
    )
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\] ==> claude: reviewing WL-1 \(round 1 of 3\)",
        statuses[2],
    )
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\] <== claude finished in \d+(?:m\d+)?s",
        statuses[3],
    )
    assert re.fullmatch(
        r"\[\d{2}:\d{2}:\d{2}\] ==> relay: ticked WL-1 in the plan",
        statuses[4],
    )
    logs = (repo / ".whyline" / "relay" / "logs").glob("*.log")
    assert all(
        "==>" not in path.read_text() and "<==" not in path.read_text()
        for path in logs
    )


def test_echo_false_prints_no_progress(repo: Path, monkeypatch, capsys):
    settings = approving_settings(repo, monkeypatch)
    loop.run_plan(
        repo,
        settings,
        repo / "plan.md",
        branch="relay/plan",
        only="WL-1",
        echo=False,
    )
    assert capsys.readouterr().out == ""


def test_only_runs_a_single_named_task(repo: Path, monkeypatch):
    settings = approving_settings(repo, monkeypatch)
    outcomes = loop.run_plan(
        repo, settings, repo / "plan.md", branch="relay/plan", only="WL-1", echo=False
    )
    assert [outcome.task_id for outcome in outcomes] == ["WL-1"]
    tasks = plan.parse((repo / "plan.md").read_text())
    assert [task.checked for task in tasks] == [True, False]


def test_a_pause_saves_resumable_state(repo: Path, monkeypatch):
    from whyline_relay import state

    base = config.load(repo)
    settings = config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, FAKE, "silent", str(repo)],
            "claude": [sys.executable, FAKE, "approve", str(repo)],
        },
        status_map=base.status_map,
    )
    with pytest.raises(loop.Paused):
        loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)
    saved = state.load(repo)
    assert saved.task_id == "WL-1"
    assert "without handing off" in saved.paused_reason
    assert plan.parse((repo / "plan.md").read_text())[0].checked is False


def test_status_after_a_blocked_pause_shows_the_question(
    repo: Path, monkeypatch, capsys
):
    base = config.load(repo)
    settings = config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, FAKE, "blocked", str(repo)],
            "claude": [sys.executable, FAKE, "approve", str(repo)],
        },
        status_map=base.status_map,
    )
    real_run = loop.agents.run

    def run_with_question(command, prompt, **kwargs):
        code = real_run(command, prompt, **kwargs)
        target = repo / ".whyline" / "active-handoff.json"
        record = json.loads(target.read_text())
        record["questions"] = ["permission needed: Bash(uv run pytest:*)"]
        target.write_text(json.dumps(record))
        return code

    monkeypatch.setattr(loop.agents, "run", run_with_question)
    with pytest.raises(loop.Paused):
        loop.run_plan(
            repo, settings, repo / "plan.md", branch="relay/plan", echo=False
        )

    assert cli.main(["status", "--repo", str(repo)]) == cli.EXIT_OK
    assert "Question: permission needed: Bash(uv run pytest:*)" in capsys.readouterr().out


def test_stop_file_prevents_starting_a_new_task(repo: Path, monkeypatch):
    settings = approving_settings(repo, monkeypatch)
    stop = repo / ".whyline" / "relay"
    stop.mkdir(parents=True, exist_ok=True)
    (stop / "STOP").write_text("")
    outcomes = loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)
    assert outcomes == []
    assert plan.parse((repo / "plan.md").read_text())[0].checked is False


def test_success_clears_state(repo: Path, monkeypatch):
    from whyline_relay import state

    settings = approving_settings(repo, monkeypatch)
    loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)
    assert state.load(repo) is None


def test_ticking_keeps_an_edit_an_agent_made_to_the_plan(repo: Path, monkeypatch):
    settings = approving_settings(repo, monkeypatch)
    planfile = repo / "plan.md"
    inner = loop.agents.run

    def edit_the_plan(command, prompt, **kwargs):
        if "review" in command:  # the implementer's turn
            with planfile.open("a") as handle:
                handle.write("- [ ] WL-3: Three\n")
        return inner(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", edit_the_plan)
    loop.run_plan(repo, settings, planfile, branch="relay/plan", only="WL-1", echo=False)
    assert "WL-3" in planfile.read_text()


def test_an_unknown_only_id_is_an_error_not_a_silent_success(repo: Path, monkeypatch):
    settings = approving_settings(repo, monkeypatch)
    with pytest.raises(plan.PlanError):
        loop.run_plan(
            repo, settings, repo / "plan.md", branch="relay/plan", only="WL-99", echo=False
        )


def test_a_pause_under_only_remembers_it(repo: Path):
    from whyline_relay import state

    base = config.load(repo)
    settings = config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, FAKE, "silent", str(repo)],
            "claude": [sys.executable, FAKE, "approve", str(repo)],
        },
        status_map=base.status_map,
    )
    with pytest.raises(loop.Paused):
        loop.run_plan(
            repo, settings, repo / "plan.md", branch="relay/plan", only="WL-2", echo=False
        )
    assert state.load(repo).only == "WL-2"


def test_resume_reuses_the_saved_base_commit(repo: Path):
    from whyline_relay import state

    base_commit = loop.gitcheck.head_commit(repo)
    (repo / "feature.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: work (WL-1)"],
        cwd=repo,
        check=True,
        capture_output=True,
    )  # the reviewer committed, then the run stopped before the box was ticked
    state.save(
        repo,
        state.RelayState(
            plan=str(repo / "plan.md"),
            branch="relay/plan",
            task_id="WL-1",
            round=1,
            base_commit=base_commit,
            paused_reason="interrupted",
            log_path="",
        ),
    )
    base = config.load(repo)
    settings = config.Config(
        plan=base.plan,
        max_rounds=base.max_rounds,
        timeout_minutes=base.timeout_minutes,
        branch_prefix=base.branch_prefix,
        agents={
            "codex": [sys.executable, FAKE, "review", str(repo)],
            "claude": [sys.executable, FAKE, "approve", str(repo)],
        },
        status_map=base.status_map,
    )
    outcomes = loop.run_plan(
        repo, settings, repo / "plan.md", branch="relay/plan",
        only="WL-1", resume=True, echo=False,
    )
    assert [outcome.task_id for outcome in outcomes] == ["WL-1"]


def head_files(repo: Path) -> list[str]:
    return subprocess.run(
        ["git", "show", "--name-only", "--format=", "HEAD"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.split()


def test_each_tick_is_committed_on_its_own_and_the_tree_stays_clean(
    repo: Path, monkeypatch
):
    settings = approving_settings(repo, monkeypatch)
    loop.run_plan(repo, settings, repo / "plan.md", branch="relay/plan", echo=False)
    assert head_files(repo) == ["plan.md"]  # the last tick, alone
    assert loop.gitcheck.dirty_paths(repo) == []
    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "chore: tick WL-1 in the plan" in log
    assert "chore: tick WL-2 in the plan" in log


def test_leftover_files_after_an_approval_pause_and_resume_recovers(
    repo: Path, monkeypatch
):
    from whyline_relay import state

    settings = approving_settings(repo, monkeypatch)
    real = loop.agents.run

    def commit_only_feature(command, prompt, **kwargs):
        code = real(command, prompt, **kwargs)
        if "approve" in command:
            (repo / "feature.py").write_text("x = 1\n")
            (repo / "stray.txt").write_text("oops\n")
            subprocess.run(["git", "add", "feature.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "feat: work (WL-1)"],
                cwd=repo, check=True, capture_output=True,
            )
        return code

    monkeypatch.setattr(loop.agents, "run", commit_only_feature)
    with pytest.raises(loop.Paused) as raised:
        loop.run_plan(
            repo, settings, repo / "plan.md", branch="relay/plan", only="WL-1", echo=False
        )
    assert "stray.txt" in raised.value.reason
    assert plan.parse((repo / "plan.md").read_text())[0].checked is False
    assert state.load(repo).task_id == "WL-1"

    (repo / "stray.txt").unlink()

    def no_agent(*args, **kwargs):
        raise AssertionError("resume of an approved task must not run an agent")

    monkeypatch.setattr(loop.agents, "run", no_agent)
    outcomes = loop.run_plan(
        repo, settings, repo / "plan.md", branch="relay/plan",
        only="WL-1", resume=True, echo=False,
    )
    assert [outcome.task_id for outcome in outcomes] == ["WL-1"]
    assert plan.parse((repo / "plan.md").read_text())[0].checked is True
    assert loop.gitcheck.dirty_paths(repo) == []


def test_resume_goes_straight_to_the_review_when_codex_already_handed_off(
    repo: Path, monkeypatch
):
    settings = approving_settings(repo, monkeypatch)
    inner = loop.agents.run
    calls: list[str] = []
    interrupt = {"on": True}

    def spy(command, prompt, **kwargs):
        agent = "codex" if "review" in command else "claude"
        calls.append(agent)
        if agent == "claude" and interrupt["on"]:
            raise KeyboardInterrupt  # Ctrl+C during the review turn
        return inner(command, prompt, **kwargs)

    monkeypatch.setattr(loop.agents, "run", spy)
    with pytest.raises(KeyboardInterrupt):
        loop.run_plan(
            repo, settings, repo / "plan.md", branch="relay/plan", only="WL-1", echo=False
        )
    interrupt["on"] = False
    calls.clear()
    loop.run_plan(
        repo, settings, repo / "plan.md", branch="relay/plan",
        only="WL-1", resume=True, echo=False,
    )
    assert calls == ["claude"]
