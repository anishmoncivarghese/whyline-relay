from pathlib import Path

from whyline_relay import state


def sample() -> state.RelayState:
    return state.RelayState(
        plan="plan.md",
        branch="relay/plan",
        task_id="WL-2",
        round=2,
        base_commit="abc123",
        paused_reason="codex exited without handing off",
        log_path="/tmp/x.log",
    )


def test_load_returns_none_when_absent(tmp_path: Path):
    assert state.load(tmp_path) is None


def test_save_then_load_round_trips(tmp_path: Path):
    state.save(tmp_path, sample())
    assert state.load(tmp_path) == sample()


def test_clear_removes_it(tmp_path: Path):
    state.save(tmp_path, sample())
    state.clear(tmp_path)
    assert state.load(tmp_path) is None


def test_corrupt_state_reads_as_absent(tmp_path: Path):
    target = tmp_path / ".whyline" / "relay"
    target.mkdir(parents=True)
    (target / "state.json").write_text("{broken")
    assert state.load(tmp_path) is None
