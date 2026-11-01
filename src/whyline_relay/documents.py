"""Documents: any kind of drafted document (a PRD, a design, a build record, a
spec, a plan) run through the planner's draft<->review machinery with its own
task id, draft file and checkpoint. A spec is a document with SPEC_KIND."""
from __future__ import annotations

import subprocess
from pathlib import Path

from whyline_relay import planner

DocKind = planner.DocKind
DocumentQuestions = planner.PlanQuestions
PLAN_KIND = planner.PLAN_KIND
SPEC_KIND = planner.SPEC_KIND
PRD_KIND = planner.PRD_KIND
DESIGN_KIND = planner.DESIGN_KIND
BUILD_RECORD_KIND = planner.BUILD_RECORD_KIND


def draft_path(root: Path, kind: DocKind) -> Path:
    return planner.draft_path(root, kind)


def draft(root, settings, kind: DocKind, request: str, *, template: str = "", attachments=(),
          print_fn=None, runner=subprocess.run) -> Path:
    return planner.draft(root, settings, request, attachments=attachments,
                         print_fn=print_fn, runner=runner, kind=kind, template=template)


def revise_text(root, settings, kind: DocKind, text: str, feedback: str, *, description: str,
                template: str = "", attachments=(), tests_text: str | None = None,
                print_fn=None, runner=subprocess.run) -> Path:
    """Seeds `text` as the kind's draft and revises it with `feedback`."""
    return planner.revise_text(root, settings, text, feedback, description=description,
                               template=template, attachments=attachments,
                               tests_text=tests_text, print_fn=print_fn, runner=runner,
                               kind=kind)


def revise(root, settings, kind: DocKind, feedback, *, print_fn=None, runner=subprocess.run) -> Path:
    return planner.revise(root, settings, feedback, print_fn=print_fn, runner=runner, kind=kind)


def resume_draft(root, settings, kind: DocKind, *, print_fn=None, runner=subprocess.run) -> Path:
    return planner.resume_draft(root, settings, print_fn=print_fn, runner=runner, kind=kind)


def answer(root, settings, kind: DocKind, answers, *, print_fn=None, runner=subprocess.run) -> Path:
    return planner.answer(root, settings, answers, print_fn=print_fn, runner=runner, kind=kind)


def discard(root, kind: DocKind) -> str:
    return planner.discard(root, kind=kind)


def pending_description(root, kind: DocKind):
    return planner.pending_description(root, kind=kind)
