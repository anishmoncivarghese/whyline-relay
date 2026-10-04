"""Specs: drafted and reviewed by the planner's machinery (spec section 3),
with their own task id, draft file and checkpoint, so a spec and a plan can
be in progress at once."""
from __future__ import annotations

import subprocess
from pathlib import Path

from whyline_relay import config, gitcheck, planner, state

SpecQuestions = planner.PlanQuestions
KIND = planner.SPEC_KIND


def draft_path(root: Path) -> Path:
    return planner.draft_path(root, KIND)


def draft(root, settings, request: str, *, attachments=(), print_fn=None, runner=subprocess.run) -> Path:
    return planner.draft(root, settings, request, attachments=attachments,
                         print_fn=print_fn, runner=runner, kind=KIND)


def revise(root, settings, feedback, *, print_fn=None, runner=subprocess.run) -> Path:
    return planner.revise(root, settings, feedback, print_fn=print_fn, runner=runner, kind=KIND)


def resume_draft(root, settings, *, print_fn=None, runner=subprocess.run) -> Path:
    return planner.resume_draft(root, settings, print_fn=print_fn, runner=runner, kind=KIND)


def answer(root, settings, answers, *, print_fn=None, runner=subprocess.run) -> Path:
    return planner.answer(root, settings, answers, print_fn=print_fn, runner=runner, kind=KIND)


def discard(root) -> str:
    return planner.discard(root, kind=KIND)


def pending_description(root):
    return planner.pending_description(root, kind=KIND)


def _slug(text: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].rstrip("-")
    return slug or "spec"


def approve(root: Path, draft: Path, *, name: str, replace: bool = False) -> Path:
    text = draft.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("the spec draft is empty")
    target = root / "docs" / "specs" / f"{_slug(name)}.md"
    if target.exists() and not replace:
        raise planner.PlanExists(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    gitcheck.commit_paths(root, [target], f"docs: add spec {_slug(name)}")
    state.clear_plan(root, name=KIND.state_name)
    return target
