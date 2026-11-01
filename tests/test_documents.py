import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whyline_relay import documents, loop, planner, prompts, specs, state
from tests.test_planner import _scripted_run, repo, settings_with_planner  # noqa: F401


def _recording(script):
    seen = []
    scripted = _scripted_run(script)

    def run(command, prompt, **kw):
        seen.append(prompt)
        return scripted(command, prompt, **kw)

    return run, seen


def test_named_kind_builds_its_own_files_and_prompts():
    kind = documents.DocKind.named("prd")
    assert kind == documents.PRD_KIND
    assert kind.task_id == "__prd__"
    assert kind.draft_name == "draft-prd.md"
    assert kind.state_name == "prd-state.json"
    assert (kind.draft_prompt, kind.review_prompt) == ("prd-draft", "prd-review")
    assert planner._Kind is documents.DocKind
    assert documents.SPEC_KIND == specs.KIND
    assert documents.BUILD_RECORD_KIND.draft_name == "draft-build-record.md"


def test_two_kinds_hold_checkpoints_at_once(repo, monkeypatch):
    settings = settings_with_planner(repo)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["codex:blocked#Q?", "codex:blocked#P?"]))
    with pytest.raises(documents.DocumentQuestions):
        documents.draft(repo, settings, documents.PRD_KIND, "prd request")
    with pytest.raises(documents.DocumentQuestions):
        documents.draft(repo, settings, documents.DESIGN_KIND, "design request")
    assert documents.pending_description(repo, documents.PRD_KIND) == "prd request"
    assert documents.pending_description(repo, documents.DESIGN_KIND) == "design request"
    assert state.load_plan(repo, name="prd-state.json") is not None
    assert state.load_plan(repo, name="design-state.json") is not None
    assert documents.pending_description(repo, documents.PLAN_KIND) is None

    assert "Discarded" in documents.discard(repo, documents.PRD_KIND)
    assert documents.pending_description(repo, documents.PRD_KIND) is None
    assert documents.pending_description(repo, documents.DESIGN_KIND) == "design request"


@pytest.mark.parametrize("name", ["prd", "design", "build-record"])
def test_document_prompts_resolve(tmp_path, name):
    draft_text = prompts.load(tmp_path, f"{name}-draft")
    review_text = prompts.load(tmp_path, f"{name}-review")
    assert f".whyline/relay/draft-{name}.md" in draft_text
    assert f".whyline/relay/draft-{name}.md" in review_text


def test_document_prompts_say_what_each_kind_needs():
    assert "R1" in prompts.PRD_DRAFT and "testable" in prompts.PRD_REVIEW
    assert "architecture" in prompts.PRD_REVIEW
    assert "options considered" in prompts.DESIGN_DRAFT
    assert "strawman" in prompts.DESIGN_REVIEW and "## Rollback" in prompts.DESIGN_REVIEW
    assert "Never invent" in prompts.BUILD_RECORD_DRAFT
    assert "every claim against the attachments" in prompts.BUILD_RECORD_REVIEW


def test_prd_draft_override_wins(repo, monkeypatch):
    override = prompts.prompts_dir(repo) / "prd-draft.md"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("OVERRIDDEN PRD\n\n## Task {task_id}\n")
    assert prompts.load(repo, "prd-draft") == "OVERRIDDEN PRD\n\n## Task {task_id}\n"

    settings = settings_with_planner(repo)
    run, seen = _recording(["claude:ready", "claude:approved"])
    monkeypatch.setattr(loop.agents, "run", run)
    documents.draft(repo, settings, documents.PRD_KIND, "a prd")
    assert "OVERRIDDEN PRD\n\n## Task __prd__" in seen[0]
    assert "OVERRIDDEN" not in seen[1]


@pytest.mark.parametrize(
    "kind, name, noun",
    [
        (documents.PRD_KIND, "prd", "the PRD"),
        (documents.DESIGN_KIND, "design", "the design"),
    ],
)
def test_draft_then_revise_round_trip(repo, monkeypatch, kind, name, noun):
    settings = settings_with_planner(repo)
    run, seen = _recording(["claude:ready", "claude:approved", "claude:ready", "claude:approved"])
    monkeypatch.setattr(loop.agents, "run", run)
    said = []

    path = documents.draft(repo, settings, kind, f"{name} request", print_fn=said.append)
    assert path == repo / f".whyline/relay/draft-{name}.md"
    assert path == documents.draft_path(repo, kind)
    assert f".whyline/relay/draft-{name}.md" in seen[0]
    assert any(f"drafting {noun}" in line for line in said)
    assert documents.pending_description(repo, kind) == f"{name} request"

    again = documents.revise(repo, settings, kind, "tighten section two")
    assert again == path
    assert len(seen) == 4
    assert "tighten section two" in seen[2]
    assert f".whyline/relay/draft-{name}.md" in seen[2]
    assert documents.pending_description(repo, kind) == f"{name} request"
    assert planner.pending_description(repo) is None


def test_announcer_names_each_kind():
    for kind, noun in [
        (documents.PRD_KIND, "the PRD"),
        (documents.DESIGN_KIND, "the design"),
        (documents.BUILD_RECORD_KIND, "the build record"),
        (documents.SPEC_KIND, "the spec"),
        (documents.PLAN_KIND, "the plan"),
    ]:
        said = []
        planner._announcer(said.append, kind)("draft", "codex")
        assert said == [f"codex is drafting {noun}"]
