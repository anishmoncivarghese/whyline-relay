from pathlib import Path

from whyline_relay import testplan

SAMPLE = """# Checks

not-covered: WL-9 \u2014 out of scope

- [ ] TP-1: Cache evicts on write
  covers: WL-1, WL-2
  kind: auto
  steps:
    - write past the bound
    - read the oldest key
  expected:
    - the oldest key is gone
  note: observed on a warm cache
- [x] TP-2: Badge is visible
  covers: WL-3
  kind: manual
  steps:
    - open the window
  expected:
    - the badge shows the count
"""

PLAN = "- [ ] WL-1: Do the work\n"


def _scenario(
    scenario_id: str,
    *,
    covers: str = "WL-1",
    kind: str = "auto",
    steps: list[str] | None = None,
    expected: list[str] | None = None,
    checked: bool = False,
) -> str:
    if steps is None:
        steps = ["run it"]
    if expected is None:
        expected = ["it works"]
    mark = "x" if checked else " "
    lines = [
        f"- [{mark}] {scenario_id}: A check",
        f"  covers: {covers}",
        f"  kind: {kind}",
        "  steps:",
        *[f"    - {step}" for step in steps],
        "  expected:",
        *[f"    - {item}" for item in expected],
    ]
    return "\n".join(lines) + "\n"


def test_parse_well_formed_file_has_every_field():
    scenarios = testplan.parse(SAMPLE)
    assert scenarios == [
        testplan.Scenario(
            id="TP-1",
            title="Cache evicts on write",
            covers=("WL-1", "WL-2"),
            kind="auto",
            steps=("write past the bound", "read the oldest key"),
            expected=("the oldest key is gone",),
            checked=False,
            note="observed on a warm cache",
        ),
        testplan.Scenario(
            id="TP-2",
            title="Badge is visible",
            covers=("WL-3",),
            kind="manual",
            steps=("open the window",),
            expected=("the badge shows the count",),
            checked=True,
            note=None,
        ),
    ]


def test_duplicate_scenario_id_is_reported():
    text = _scenario("TP-1") + _scenario("TP-1")
    assert testplan.validate(text, PLAN) == ["duplicate scenario id TP-1"]


def test_covers_id_missing_from_the_plan_is_reported():
    text = _scenario("TP-1", covers="WL-1, NO-SUCH")
    assert testplan.validate(text, PLAN) == [
        "TP-1 covers NO-SUCH, which is not in the plan"
    ]


def test_kind_other_than_auto_or_manual_is_reported():
    text = _scenario("TP-1", kind="sketch")
    assert testplan.validate(text, PLAN) == [
        "TP-1 has kind 'sketch', expected auto or manual"
    ]


def test_scenario_with_no_steps_is_reported():
    text = _scenario("TP-1", steps=[])
    assert testplan.validate(text, PLAN) == ["TP-1 has no steps"]


def test_scenario_with_no_expected_lines_is_reported():
    text = _scenario("TP-1", expected=[])
    assert testplan.validate(text, PLAN) == ["TP-1 has no expected lines"]


def test_uncovered_plan_task_is_reported():
    plan = "- [ ] WL-1: Do the work\n- [ ] WL-2: Also do this\n"
    assert testplan.validate(_scenario("TP-1"), plan) == ["WL-2 is not covered"]


def test_release_and_verify_tasks_need_no_scenario():
    plan = (
        "- [ ] WL-1: Do the work\n"
        "- [ ] REL-1: Ship it\n"
        "  relay-profile: release\n"
        "- [ ] VER-1: Check the phase\n"
        "  relay-profile: verify\n"
    )
    assert testplan.validate(_scenario("TP-1"), plan) == []
    assert testplan.validate("", plan) == ["WL-1 is not covered"]


def test_not_covered_line_silences_a_task():
    plan = "- [ ] WL-1: Do the work\n- [ ] WL-2: Leave this\n"
    text = "not-covered: WL-2 \u2014 no behaviour to assert\n\n" + _scenario("TP-1")
    assert testplan.validate(text, plan) == []


def test_open_manual_ignores_checked_auto_and_other_tasks():
    text = """
- [ ] TP-1: Look at the badge
  covers: WL-1
  kind: manual
  steps:
    - open the window
  expected:
    - the badge is visible
- [x] TP-2: Badge was checked
  covers: WL-1
  kind: manual
  steps:
    - open the window
  expected:
    - the badge is visible
- [ ] TP-3: Badge count is stored
  covers: WL-1
  kind: auto
  steps:
    - read the count
  expected:
    - the count is one
- [ ] TP-4: Other screen
  covers: WL-9
  kind: manual
  steps:
    - open the other screen
  expected:
    - the other screen is shown
- [ ] TP-5: Badge and tray
  covers: WL-1, WL-9
  kind: manual
  steps:
    - open the window
  expected:
    - both are visible
"""
    opened = testplan.open_manual(text, ["WL-1"])
    assert [scenario.id for scenario in opened] == ["TP-1", "TP-5"]
    assert opened[0] == testplan.Scenario(
        id="TP-1",
        title="Look at the badge",
        covers=("WL-1",),
        kind="manual",
        steps=("open the window",),
        expected=("the badge is visible",),
        checked=False,
        note=None,
    )
    assert all(scenario.kind == "manual" and not scenario.checked for scenario in opened)
    assert [scenario.id for scenario in testplan.open_manual(text, ["WL-9"])] == [
        "TP-4",
        "TP-5",
    ]
    assert testplan.open_manual(text, ["WL-0"]) == []


def test_path_for_maps_plan_name_and_plain_md():
    assert testplan.path_for(Path("plans/cache.plan.md")) == Path("plans/cache.tests.md")
    assert testplan.path_for(Path("plans/cache.md")) == Path("plans/cache.tests.md")


def test_pause_reason_is_one_line_per_scenario():
    text = (
        _scenario("TP-1", kind="manual", steps=["open the window"], expected=["the badge shows"])
        + _scenario("TP-2", kind="manual", steps=["click the tray"], expected=["the menu opens"])
    )
    reason = testplan.pause_reason("T-2", testplan.open_manual(text, ["WL-1"]))
    lines = reason.splitlines()
    assert lines == [
        "Manual checks before T-2:",
        "TP-1: A check | steps: open the window | expected: the badge shows",
        "TP-2: A check | steps: click the tray | expected: the menu opens",
    ]


def test_mark_passed_ticks_and_adds_a_note_without_touching_the_next():
    text = _scenario("TP-1", kind="manual") + _scenario("TP-2", kind="manual")
    updated = testplan.mark_passed(text, "TP-1", "looks right")
    scenarios = testplan.parse(updated)
    assert scenarios[0].checked and scenarios[0].note == "looks right"
    assert not scenarios[1].checked and scenarios[1].note is None
    assert "- [x] TP-1:" in updated
    assert "- [ ] TP-2:" in updated


def test_mark_passed_without_a_note_leaves_the_note_unset():
    updated = testplan.mark_passed(_scenario("TP-1", kind="manual"), "TP-1")
    assert testplan.parse(updated)[0].note is None
    assert testplan.parse(updated)[0].checked
    assert "note:" not in updated


def test_mark_passed_inserts_a_note_when_the_file_has_no_trailing_newline():
    text = (
        "- [ ] TP-1: Badge\n"
        "  covers: WL-1\n"
        "  kind: manual\n"
        "  steps:\n"
        "    - open it\n"
        "  expected:\n"
        "    - it shows"
    )
    updated = testplan.mark_passed(text, "TP-1", "fine")
    scenario = testplan.parse(updated)[0]
    assert scenario.checked and scenario.note == "fine"
    assert updated.endswith("\n  note: fine\n") or updated.endswith("note: fine\n")


def test_mark_failed_records_the_note_and_stays_open():
    updated = testplan.mark_failed(_scenario("TP-1", kind="manual"), "TP-1", "overlaps")
    scenario = testplan.parse(updated)[0]
    assert scenario.note == "FAILED overlaps"
    assert not scenario.checked
    assert [item.id for item in testplan.open_manual(updated, ["WL-1"])] == ["TP-1"]


def test_mark_failed_rejects_a_blank_note():
    try:
        testplan.mark_failed(_scenario("TP-1", kind="manual"), "TP-1", "  ")
    except ValueError as error:
        assert "note" in str(error)
    else:
        raise AssertionError("a blank failure note was accepted")
