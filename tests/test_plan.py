import pytest

from whyline_relay import plan

SAMPLE = """# Plan

- [x] WL-0: Scaffold the module
- [ ] WL-1: Add bounded cache invalidation
      Cache must evict on write, not on a timer.
      Keep the public API unchanged.
- [ ] WL-2: Add cache metrics
"""


def test_parse_finds_every_task_in_order():
    tasks = plan.parse(SAMPLE)
    assert [task.task_id for task in tasks] == ["WL-0", "WL-1", "WL-2"]
    assert [task.checked for task in tasks] == [True, False, False]


def test_task_text_includes_dedented_detail_block():
    tasks = plan.parse(SAMPLE)
    assert tasks[1].text == (
        "WL-1: Add bounded cache invalidation\n"
        "Cache must evict on write, not on a timer.\n"
        "Keep the public API unchanged."
    )


def test_task_without_detail_is_just_its_title():
    tasks = plan.parse(SAMPLE)
    assert tasks[2].text == "WL-2: Add cache metrics"


def test_next_unchecked_skips_checked_tasks():
    assert plan.next_unchecked(plan.parse(SAMPLE)).task_id == "WL-1"


def test_next_unchecked_returns_none_when_all_done():
    assert plan.next_unchecked(plan.parse("- [x] WL-1: Done\n")) is None


def test_tick_marks_only_the_named_task():
    updated = plan.tick(SAMPLE, "WL-1")
    tasks = plan.parse(updated)
    assert [task.checked for task in tasks] == [True, True, False]


def test_tick_preserves_every_other_line_exactly():
    updated = plan.tick(SAMPLE, "WL-2")
    assert updated.splitlines()[0] == "# Plan"
    assert "Cache must evict on write, not on a timer." in updated
    assert updated.count("- [ ]") == 1


def test_tick_on_unknown_task_raises():
    with pytest.raises(plan.PlanError):
        plan.tick(SAMPLE, "WL-99")


def test_parse_rejects_duplicate_task_ids():
    with pytest.raises(plan.PlanError):
        plan.parse("- [ ] WL-1: One\n- [ ] WL-1: Two\n")


def test_checkbox_line_without_colon_uses_whole_line_as_id():
    tasks = plan.parse("- [ ] WL-7 do the thing\n")
    assert tasks[0].task_id == "WL-7"
    assert tasks[0].text == "WL-7 do the thing"
