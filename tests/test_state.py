import json
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


def sample_plan() -> state.PlanState:
    return state.PlanState(
        description="add a health-check endpoint",
        stage="review",
        round=2,
        stage_visits={"draft": 1, "review": 1},
        agent="claude",
        feedback="",
        draft_path="/tmp/draft-plan.md",
        paused_reason="",
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


def test_pipeline_fields_round_trip(tmp_path: Path):
    value = state.RelayState(
        plan="plan.md",
        branch="relay/T-1",
        task_id="T-1",
        round=2,
        base_commit="abc123",
        paused_reason="stopped",
        log_path="",
        profile="full",
        stage="test",
        stage_visits={"draft": 1, "test": 1},
        pipeline_fingerprint="deadbeef",
    )
    state.save(tmp_path, value)
    loaded = state.load(tmp_path)
    assert loaded == value


def test_a_state_file_saved_before_this_field_existed_still_loads(tmp_path: Path):
    # An older relay's state.json has none of these keys; RelayState(**record)
    # must fill them from defaults rather than raise.
    target = state.path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "plan": "plan.md",
                "branch": "relay/T-1",
                "task_id": "T-1",
                "round": 1,
                "base_commit": "abc",
                "paused_reason": "x",
                "log_path": "",
            }
        )
    )
    loaded = state.load(tmp_path)
    assert loaded.profile == "" and loaded.stage == ""
    assert loaded.stage_visits == {} and loaded.pipeline_fingerprint == ""


def test_load_plan_returns_none_when_absent(tmp_path: Path):
    assert state.load_plan(tmp_path) is None


def test_save_then_load_plan_round_trips(tmp_path: Path):
    state.save_plan(tmp_path, sample_plan())
    assert state.load_plan(tmp_path) == sample_plan()


def test_clear_plan_removes_it(tmp_path: Path):
    state.save_plan(tmp_path, sample_plan())
    state.clear_plan(tmp_path)
    assert state.load_plan(tmp_path) is None


def test_corrupt_plan_state_reads_as_absent(tmp_path: Path):
    target = tmp_path / ".whyline" / "relay"
    target.mkdir(parents=True)
    (target / "plan-state.json").write_text("{broken")
    assert state.load_plan(tmp_path) is None


def test_plan_state_and_relay_state_live_at_different_paths(tmp_path: Path):
    state.save(tmp_path, sample())
    state.save_plan(tmp_path, sample_plan())
    assert state.path(tmp_path) != state.plan_path(tmp_path)
    assert state.load(tmp_path) == sample()
    assert state.load_plan(tmp_path) == sample_plan()
