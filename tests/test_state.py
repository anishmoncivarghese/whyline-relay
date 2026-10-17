import errno
import json
from pathlib import Path

import pytest

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
    assert loaded.timeout_minutes is None


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


def test_a_plan_file_saved_before_timeout_minutes_existed_still_loads(tmp_path: Path):
    target = state.plan_path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "description": "add a health-check endpoint",
                "stage": "review",
                "round": 1,
                "stage_visits": {"draft": 1},
                "agent": "codex",
                "feedback": "",
                "draft_path": "plan.md",
                "paused_reason": "",
                "log_path": "",
            }
        )
    )
    loaded = state.load_plan(tmp_path)
    assert loaded is not None
    assert loaded.timeout_minutes is None
    assert loaded.attachments == []


def test_timeout_minutes_round_trips_zero_and_a_positive_cap(tmp_path: Path):
    for minutes in (0, 30):
        relay = state.RelayState(
            plan="plan.md",
            branch="relay/plan",
            task_id="WL-1",
            round=1,
            base_commit="abc",
            paused_reason="paused",
            log_path="",
            timeout_minutes=minutes,
        )
        state.save(tmp_path, relay)
        assert state.load(tmp_path).timeout_minutes == minutes
        plan = state.PlanState(
            description="add a health-check endpoint",
            stage="draft",
            round=1,
            stage_visits={"draft": 1},
            agent="",
            feedback="",
            draft_path="plan.md",
            paused_reason="",
            log_path="",
            timeout_minutes=minutes,
        )
        state.save_plan(tmp_path, plan)
        assert state.load_plan(tmp_path).timeout_minutes == minutes


def test_load_does_not_strip_an_unknown_key(tmp_path: Path):
    # An older relay must fail RelayState(**record) on a key it does not know.
    # load() already turns that TypeError into None; it must not drop the key.
    target = state.path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "plan": "plan.md",
                "branch": "relay/plan",
                "task_id": "WL-1",
                "round": 1,
                "base_commit": "abc",
                "paused_reason": "paused",
                "log_path": "",
                "not_a_field": 1,
            }
        )
    )
    assert state.load(tmp_path) is None


def test_plan_state_and_relay_state_live_at_different_paths(tmp_path: Path):
    state.save(tmp_path, sample())
    state.save_plan(tmp_path, sample_plan())
    assert state.path(tmp_path) != state.plan_path(tmp_path)
    assert state.load(tmp_path) == sample()
    assert state.load_plan(tmp_path) == sample_plan()


@pytest.mark.parametrize("errno_code", [errno.ENOSPC, errno.EDQUOT])
def test_save_on_a_full_disk_keeps_the_old_file_and_raises(
    tmp_path: Path, monkeypatch, errno_code: int
):
    original = sample()
    state.save(tmp_path, original)
    target = state.path(tmp_path)
    before = target.read_bytes()
    real_write = Path.write_text

    def full(self, data, *args, **kwargs):
        if self.name.endswith(".json.tmp"):
            real_write(self, "partial", encoding="utf-8")
            raise OSError(errno_code, "No space left on device")
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", full)
    replacement = state.RelayState(
        plan=original.plan,
        branch=original.branch,
        task_id="WL-9",
        round=1,
        base_commit=original.base_commit,
        paused_reason="other",
        log_path=original.log_path,
    )
    with pytest.raises(state.DiskFull) as raised:
        state.save(tmp_path, replacement)
    assert target.read_bytes() == before
    assert state.load(tmp_path) == original
    assert list(target.parent.glob("*.json.tmp")) == []
    assert raised.value.path == target
    assert raised.value.free_bytes >= 0


def test_save_plan_on_a_full_disk_keeps_the_old_file_and_raises(
    tmp_path: Path, monkeypatch
):
    original = sample_plan()
    state.save_plan(tmp_path, original)
    target = state.plan_path(tmp_path)
    before = target.read_bytes()
    real_write = Path.write_text

    def full(self, data, *args, **kwargs):
        if self.name.endswith(".json.tmp"):
            real_write(self, "partial", encoding="utf-8")
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", full)
    replacement = state.PlanState(
        description="something else",
        stage=original.stage,
        round=original.round,
        stage_visits=original.stage_visits,
        agent=original.agent,
        feedback=original.feedback,
        draft_path=original.draft_path,
        paused_reason=original.paused_reason,
        log_path=original.log_path,
    )
    with pytest.raises(state.DiskFull) as raised:
        state.save_plan(tmp_path, replacement)
    assert target.read_bytes() == before
    assert state.load_plan(tmp_path) == original
    assert list(target.parent.glob("*.json.tmp")) == []
    assert raised.value.path == target
