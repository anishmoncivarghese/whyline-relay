"""The test plan beside a plan: scenarios, coverage, and manual checks."""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from whyline_relay import plan

CHECKBOX = re.compile(r"^(?P<indent>\s*)- \[(?P<mark>[ xX])\]\s+(?P<body>.*)$")
KINDS = frozenset({"auto", "manual"})
EXEMPT_PROFILES = frozenset({"release", "verify"})
# The pause reason starts with this. One line per scenario follows it:
# `TP-1: Title | steps: a; b | expected: c; d`.
MANUAL_PREFIX = "Manual checks before "
# Task ids contain hyphens (`WL-1`). A reason may sit against the id only
# when the separator is one of these, not a single hyphen.
_REASON_MARKS = ("\u2014", "\u2013", "--")


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    covers: tuple[str, ...]
    kind: str
    steps: tuple[str, ...]
    expected: tuple[str, ...]
    checked: bool
    note: str | None = None


def path_for(plan_path: Path) -> Path:
    """`x.plan.md` and any other `x.md` both name `x.tests.md` beside the plan."""
    path = Path(plan_path)
    name = path.name
    if name.endswith(".plan.md"):
        stem = name[: -len(".plan.md")]
    elif name.endswith(".md"):
        stem = name[: -len(".md")]
    else:
        stem = name
    return path.with_name(f"{stem}.tests.md")


def parse(text: str) -> list[Scenario]:
    """Parse scenarios in file order. Indented lines below one are its detail."""
    lines = text.splitlines()
    scenarios: list[Scenario] = []
    index = 0
    while index < len(lines):
        match = CHECKBOX.match(lines[index])
        if match is None:
            index += 1
            continue
        scenario_id, title = _heading(match.group("body").strip())
        detail: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            following = lines[cursor]
            if CHECKBOX.match(following) or (
                following.strip() and not following.startswith((" ", "\t"))
            ):
                break
            detail.append(following)
            cursor += 1
        covers, kind, steps, expected, note = _detail(detail)
        scenarios.append(
            Scenario(
                id=scenario_id,
                title=title,
                covers=covers,
                kind=kind,
                steps=steps,
                expected=expected,
                checked=match.group("mark") != " ",
                note=note,
            )
        )
        index = cursor
    return scenarios


def validate(test_text: str, plan_text: str) -> list[str]:
    """One problem line per gap between the test plan and the plan."""
    try:
        tasks = plan.parse(plan_text)
    except plan.PlanError as error:
        return [str(error)]
    plan_ids = {task.task_id for task in tasks}
    exempt = {task.task_id for task in tasks if task.profile in EXEMPT_PROFILES}
    silenced = _not_covered_ids(test_text)
    problems: list[str] = []
    seen: set[str] = set()
    covered: set[str] = set()
    for scenario in parse(test_text):
        if scenario.id in seen:
            problems.append(f"duplicate scenario id {scenario.id}")
        else:
            seen.add(scenario.id)
        for task_id in scenario.covers:
            if task_id in plan_ids:
                covered.add(task_id)
            else:
                problems.append(f"{scenario.id} covers {task_id}, which is not in the plan")
        if scenario.kind not in KINDS:
            problems.append(
                f"{scenario.id} has kind {scenario.kind!r}, expected auto or manual"
            )
        if not scenario.steps:
            problems.append(f"{scenario.id} has no steps")
        if not scenario.expected:
            problems.append(f"{scenario.id} has no expected lines")
    for task in tasks:
        if task.task_id in exempt or task.task_id in covered or task.task_id in silenced:
            continue
        problems.append(f"{task.task_id} is not covered")
    return problems


def open_manual(test_text: str, task_ids: Collection[str]) -> list[Scenario]:
    """Unchecked manual scenarios that cover any of `task_ids`, in file order."""
    wanted = set(task_ids)
    return [
        scenario
        for scenario in parse(test_text)
        if scenario.kind == "manual"
        and not scenario.checked
        and wanted.intersection(scenario.covers)
    ]


def scenario_line(scenario: Scenario) -> str:
    """One pause line: the id, the title, the steps, and the expected lines."""
    steps = "; ".join(scenario.steps)
    expected = "; ".join(scenario.expected)
    return f"{scenario.id}: {scenario.title} | steps: {steps} | expected: {expected}"


def pause_reason(task_id: str, scenarios: Sequence[Scenario]) -> str:
    """`Manual checks before <task id>:` and one line per scenario.

    An empty list is only the header. Callers pause when the list is not empty.
    """
    header = f"{MANUAL_PREFIX}{task_id}:"
    body = "\n".join(scenario_line(scenario) for scenario in scenarios)
    if not body:
        return header
    return f"{header}\n{body}"


def mark_passed(text: str, scenario_id: str, note: str | None = None) -> str:
    """Tick `scenario_id`. When `note` is given, set its `note:` line."""
    cleaned = None if note is None else _clean_note(note)
    return _rewrite(text, scenario_id, tick=True, note=cleaned or None)


def mark_failed(text: str, scenario_id: str, note: str) -> str:
    """Record `note: FAILED <note>` and leave the checkbox unticked."""
    cleaned = _clean_note(note)
    if not cleaned:
        raise ValueError("a failure note is required")
    return _rewrite(text, scenario_id, tick=False, note=f"FAILED {cleaned}")


def _clean_note(note: str) -> str:
    if any(character in note for character in "\r\n"):
        raise ValueError("a note must be one line")
    return note.strip()


def _rewrite(text: str, scenario_id: str, *, tick: bool, note: str | None) -> str:
    lines = text.splitlines(keepends=True)
    start, end = _scenario_span(lines, scenario_id)
    if tick:
        lines[start] = lines[start].replace("- [ ]", "- [x]", 1)
    if note is not None:
        _set_note(lines, start, end, note)
    return "".join(lines)


def _scenario_span(lines: list[str], scenario_id: str) -> tuple[int, int]:
    for index, raw in enumerate(lines):
        match = CHECKBOX.match(raw.rstrip("\r\n"))
        if match is None:
            continue
        found, _title = _heading(match.group("body").strip())
        if found != scenario_id:
            continue
        end = index + 1
        while end < len(lines):
            following = lines[end].rstrip("\r\n")
            if CHECKBOX.match(following) or (
                following.strip() and not following.startswith((" ", "\t"))
            ):
                break
            end += 1
        return index, end
    raise ValueError(f"no scenario {scenario_id}")


def _set_note(lines: list[str], start: int, end: int, note: str) -> None:
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        if not stripped.startswith("note:"):
            continue
        indent = lines[index][: len(lines[index]) - len(lines[index].lstrip(" \t"))]
        lines[index] = f"{indent}note: {note}{_newline(lines[index])}"
        return
    indent = "  "
    for index in range(start + 1, end):
        if lines[index].strip():
            indent = lines[index][: len(lines[index]) - len(lines[index].lstrip(" \t"))]
            break
    if end > 0 and not lines[end - 1].endswith("\n"):
        lines[end - 1] = lines[end - 1] + "\n"
    ending = _newline(lines[start]) or "\n"
    lines.insert(end, f"{indent}note: {note}{ending}")


def _newline(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _heading(body: str) -> tuple[str, str]:
    head, separator, tail = body.partition(":")
    if separator:
        return head.strip(), tail.strip()
    parts = body.split()
    return (parts[0], "") if parts else ("", "")


def _detail(
    lines: list[str],
) -> tuple[tuple[str, ...], str, tuple[str, ...], tuple[str, ...], str | None]:
    covers: list[str] = []
    kind = ""
    steps: list[str] = []
    expected: list[str] = []
    note: str | None = None
    section: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("covers:"):
            section = None
            covers = [part.strip() for part in line.removeprefix("covers:").split(",") if part.strip()]
        elif line.startswith("kind:"):
            section = None
            kind = line.removeprefix("kind:").strip()
        elif line.startswith("steps:"):
            section = "steps"
            steps = _items_on_header(line.removeprefix("steps:"))
        elif line.startswith("expected:"):
            section = "expected"
            expected = _items_on_header(line.removeprefix("expected:"))
        elif line.startswith("note:"):
            section = None
            note = line.removeprefix("note:").strip()
        elif section in {"steps", "expected"} and line.startswith("- "):
            item = line[2:].strip()
            if not item:
                continue
            if section == "steps":
                steps.append(item)
            else:
                expected.append(item)
    return tuple(covers), kind, tuple(steps), tuple(expected), note


def _items_on_header(rest: str) -> list[str]:
    text = rest.strip()
    if not text.startswith("- "):
        return []
    item = text[2:].strip()
    return [item] if item else []


def _not_covered_ids(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("not-covered:"):
            continue
        rest = line[len("not-covered:") :].strip()
        if not rest:
            continue
        token = rest.split()[0]
        for mark in _REASON_MARKS:
            if mark in token:
                token = token.split(mark, 1)[0]
                break
        if token:
            found.add(token)
    return found
