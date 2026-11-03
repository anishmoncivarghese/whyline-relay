"""A turn STOP or Ctrl+C kills is recorded, and the next turn is told to
continue from the changes it left rather than start over."""

import json
import re
import subprocess
from pathlib import Path

from whyline_relay import cli, loop, prompts, state

PIPELINE_CONFIG = (
    '[roles]\nimplementer = "codex"\ntester = "claude"\nreviewer = "claude"\n'
    '[pipeline]\ndefault_profile = "full"\n'
    '[pipeline.profiles]\nfull = ["draft", "test", "review"]\n'
    '[pipeline.stages.draft]\nrole = "implementer"\nprompt = "implement"\n'
    '[pipeline.stages.draft.on]\nready = "@next"\n'
    '[pipeline.stages.test]\nrole = "tester"\nprompt = "test"\n'
    '[pipeline.stages.test.on]\npassed = "@next"\nfailed = "draft"\n'
    '[pipeline.stages.review]\nrole = "reviewer"\nprompt = "review"\n'
    '[pipeline.stages.review.on]\napproved = "@complete"\nrejected = "draft"\n'
    '[agents.codex]\ncommand = ["codex"]\n'
    '[agents.claude]\ncommand = ["claude"]\n'
)
NOTE = (
    "The previous turn (implementer codex) was cut off before it handed off. "
    "It left changes in: src/work.py. Continue from them; don't start over "
    "and don't discard them."
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(two_role_repo) -> Path:
    root = two_role_repo("- [ ] T-1: build\n")
    (root / ".whyline/relay/config.toml").write_text(PIPELINE_CONFIG)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "config")
    return root


def _fake_agent(monkeypatch, turns: list[dict]) -> list[str]:
    """Stand in for agents.run. A turn with `cut` writes src/work.py and is
    killed: "ctrl-c" raises KeyboardInterrupt, "stop" writes STOP and
    raises AgentCancelled, "now" does what `pause --now` does first. Any
    other turn hands off with to/status and may ask for a `pause`.
    Returns every prompt, in order."""
    seen: list[str] = []

    def run(command, prompt, *, cwd, log_path, **kwargs):
        turn = turns.pop(0)
        root = Path(cwd)
        seen.append(prompt)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok\n")
        cut = turn.get("cut")
        if cut:
            (root / "src").mkdir(exist_ok=True)
            (root / "src" / "work.py").write_text("half done\n")
            if cut == "ctrl-c":
                raise KeyboardInterrupt
            if cut == "now":
                loop.request_pause(root, now=True)
            loop.stop_path(root).write_text("")
            raise loop.agents.AgentCancelled("codex cancelled by user")
        if turn.get("pause"):
            loop.request_pause(root)
        task_id = re.search(r"^## Task (\S+)", prompt, re.M).group(1)
        target = root / ".whyline" / "active-handoff.json"
        previous = json.loads(target.read_text()) if target.exists() else {}
        counter = int(previous.get("counter", 0)) + 1
        target.write_text(
            json.dumps(
                {
                    "id": f"event{counter}",
                    "counter": counter,
                    "task": task_id,
                    "to_actor": turn["to"],
                    "status": turn["status"],
                    "summary": f"{turn['status']} summary",
                    "from_actor": "",
                }
            )
        )
        return 0

    monkeypatch.setattr(loop.agents, "run", run)
    return seen


def _start(root: Path) -> int:
    return cli.main(["start", "--repo", str(root)])


def _resume(root: Path) -> int:
    return cli.main(["resume", "--repo", str(root), "--allow-dirty"])


def test_ctrl_c_mid_turn_records_the_files_it_left(two_role_repo, monkeypatch, capsys):
    root = _repo(two_role_repo)
    _fake_agent(monkeypatch, [{"cut": "ctrl-c"}])

    assert _start(root) == cli.EXIT_PAUSED
    saved = state.load(root)
    assert saved.paused_reason == "interrupted by the user (Ctrl+C)"
    assert saved.cut_off == {
        "stage": "draft",
        "role": "implementer",
        "agent": "codex",
        "changed_files": ["src/work.py"],
    }
    assert not loop.stop_path(root).exists()
    assert not loop.pause_path(root).exists()


def test_a_stop_keeps_its_reason_and_records_the_cut_off(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo)
    _fake_agent(monkeypatch, [{"cut": "stop"}])

    assert _start(root) == cli.EXIT_PAUSED
    saved = state.load(root)
    assert saved.paused_reason == "codex cancelled by user"
    assert saved.cut_off["changed_files"] == ["src/work.py"]
    assert not loop.stop_path(root).exists()


def test_pause_now_gives_the_paused_now_reason(two_role_repo, monkeypatch, capsys):
    root = _repo(two_role_repo)
    _fake_agent(monkeypatch, [{"cut": "now"}])

    assert _start(root) == cli.EXIT_PAUSED
    assert (
        "Paused: paused now by you; codex's turn was cut off"
        in capsys.readouterr().err
    )
    saved = state.load(root)
    assert saved.paused_reason == "paused now by you; codex's turn was cut off"
    assert saved.cut_off["agent"] == "codex"
    assert not loop.stop_path(root).exists()
    assert not loop.pause_path(root).exists()


def test_the_resume_prompt_names_the_files_and_only_once(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo)
    seen = _fake_agent(
        monkeypatch,
        [
            {"cut": "ctrl-c"},
            {"to": "claude", "status": "ready"},
            {"to": "claude", "status": "passed"},
            {"to": "claude", "status": "approved"},
        ],
    )
    assert _start(root) == cli.EXIT_PAUSED

    # The fake reviewer never commits, so the approval pauses unverified.
    _resume(root)
    assert seen[1].startswith(NOTE)
    assert all("was cut off" not in prompt for prompt in seen[2:])
    assert len(seen) == 4


def test_cut_off_is_cleared_once_the_next_turn_starts(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo)
    seen = _fake_agent(
        monkeypatch,
        [{"cut": "ctrl-c"}, {"to": "claude", "status": "ready", "pause": True}],
    )
    assert _start(root) == cli.EXIT_PAUSED
    assert state.load(root).cut_off is not None

    assert _resume(root) == cli.EXIT_PAUSED
    assert seen[1].startswith(NOTE)
    saved = state.load(root)
    assert saved.paused_reason == loop.PAUSED_BY_USER
    assert saved.cut_off is None


def test_a_pause_before_the_next_turn_keeps_the_cut_off(
    two_role_repo, monkeypatch, capsys
):
    root = _repo(two_role_repo)
    _fake_agent(monkeypatch, [{"cut": "ctrl-c"}])
    assert _start(root) == cli.EXIT_PAUSED
    recorded = state.load(root).cut_off

    def refuse(*args, **kwargs):
        raise loop.Paused("refused before the turn")

    monkeypatch.setattr(loop, "_refuse_low_disk", refuse)
    assert _resume(root) == cli.EXIT_PAUSED
    assert state.load(root).cut_off == recorded


def test_the_preamble_says_when_no_files_changed():
    text = prompts.cut_off_preamble(
        {"stage": "test", "role": "tester", "agent": "claude", "changed_files": []}
    )
    assert text.startswith(
        "The previous turn (tester claude) was cut off before it handed off. "
        "It left changes in: no files."
    )
