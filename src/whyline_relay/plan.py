"""Reading and updating the Markdown task checklist."""

from __future__ import annotations

import re
from dataclasses import dataclass

CHECKBOX = re.compile(r"^(?P<indent>\s*)- \[(?P<mark>[ xX])\]\s+(?P<body>.*)$")
RELAY_PROFILE = re.compile(r"(?m)^relay-profile:\s*(\S+)\s*$")


class PlanError(ValueError):
    """The plan file cannot be used as written."""


@dataclass(frozen=True)
class Task:
    task_id: str
    text: str
    checked: bool
    line_index: int
    profile: str | None = None


def _task_id(body: str) -> str:
    head, separator, _ = body.partition(":")
    candidate = head if separator else body.split()[0] if body.split() else ""
    return candidate.strip()


def _dedent(lines: list[str]) -> list[str]:
    stripped = [line for line in lines if line.strip()]
    if not stripped:
        return []
    common = min(len(line) - len(line.lstrip()) for line in stripped)
    return [line[common:].rstrip() for line in lines if line.strip()]


def parse(content: str) -> list[Task]:
    """Parse checklist items in file order. Indented lines below one are its detail."""
    lines = content.splitlines()
    tasks: list[Task] = []
    seen: set[str] = set()
    index = 0
    while index < len(lines):
        match = CHECKBOX.match(lines[index])
        if match is None:
            index += 1
            continue
        body = match.group("body").strip()
        task_id = _task_id(body)
        if not task_id:
            raise PlanError(f"line {index + 1}: checklist item has no task id")
        if task_id in seen:
            raise PlanError(f"line {index + 1}: duplicate task id {task_id!r}")
        seen.add(task_id)
        detail: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            following = lines[cursor]
            if CHECKBOX.match(following) or (following.strip() and not following.startswith((" ", "\t"))):
                break
            detail.append(following)
            cursor += 1
        text = "\n".join([body, *_dedent(detail)])
        profile_match = RELAY_PROFILE.search(text)
        tasks.append(
            Task(
                task_id=task_id,
                text=text,
                checked=match.group("mark") != " ",
                line_index=index,
                profile=profile_match.group(1) if profile_match else None,
            )
        )
        index = cursor
    return tasks


def next_unchecked(tasks: list[Task]) -> Task | None:
    for task in tasks:
        if not task.checked:
            return task
    return None


def find(tasks: list[Task], task_id: str) -> Task | None:
    for task in tasks:
        if task.task_id == task_id:
            return task
    return None


def tick(content: str, task_id: str) -> str:
    """Return `content` with `task_id`'s checkbox marked done. Only that line changes."""
    task = find(parse(content), task_id)
    if task is None:
        raise PlanError(f"no task {task_id!r} in the plan")
    lines = content.splitlines(keepends=True)
    line = lines[task.line_index]
    lines[task.line_index] = line.replace("- [ ]", "- [x]", 1)
    return "".join(lines)
