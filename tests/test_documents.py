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


TEMPLATE = "## Goals\n\n## Risks"
EDIT_SENTENCE = "Start from the existing draft at .whyline/relay/draft-prd.md"


def test_template_is_in_the_draft_prompt_and_survives_revise(repo, monkeypatch):
    settings = settings_with_planner(repo)
    run, seen = _recording(["claude:ready", "claude:approved", "claude:ready", "claude:approved"])
    monkeypatch.setattr(loop.agents, "run", run)

    documents.draft(repo, settings, documents.PRD_KIND, "a prd", template=TEMPLATE)
    assert f"\n\nUse this structure:\n\n{TEMPLATE}" in seen[0]
    assert "Use this structure" not in seen[1]
    assert state.load_plan(repo, name="prd-state.json").template == TEMPLATE

    documents.revise(repo, settings, documents.PRD_KIND, "shorter")
    assert f"\n\nUse this structure:\n\n{TEMPLATE}" in seen[2]
    assert state.load_plan(repo, name="prd-state.json").template == TEMPLATE


def test_an_empty_template_changes_nothing(repo, monkeypatch):
    settings = settings_with_planner(repo)
    run, seen = _recording(["claude:ready", "claude:approved"])
    monkeypatch.setattr(loop.agents, "run", run)
    documents.draft(repo, settings, documents.PRD_KIND, "a prd")
    assert "Use this structure" not in seen[0]
    assert state.load_plan(repo, name="prd-state.json").template == ""


def test_revise_text_seeds_the_text_and_asks_for_an_edit(repo, monkeypatch):
    settings = settings_with_planner(repo)
    notes = repo / "notes.md"
    notes.write_text("context\n")
    run, seen = _recording(["claude:ready", "claude:approved"])
    monkeypatch.setattr(loop.agents, "run", run)

    path = documents.revise_text(
        repo, settings, documents.PRD_KIND, "# Old PRD\n", "add a risks section",
        description="the checkout PRD", template=TEMPLATE, attachments=[notes],
    )
    assert path == documents.draft_path(repo, documents.PRD_KIND)
    assert path.read_text() == "# Old PRD\n"
    assert len(seen) == 2
    assert EDIT_SENTENCE in seen[0]
    assert "do not rewrite it from nothing" in seen[0]
    assert "add a risks section" in seen[0]
    assert f"Use this structure:\n\n{TEMPLATE}" in seen[0]
    assert EDIT_SENTENCE not in seen[1]
    saved = state.load_plan(repo, name="prd-state.json")
    assert saved is not None and saved.stage == "@complete"
    assert saved.description == "the checkout PRD"
    assert saved.template == TEMPLATE
    assert saved.attachments == ["notes.md"]


def test_revise_text_of_an_empty_text_does_not_ask_for_an_edit(repo, monkeypatch):
    settings = settings_with_planner(repo)
    run, seen = _recording(["claude:ready", "claude:approved"])
    monkeypatch.setattr(loop.agents, "run", run)
    documents.revise_text(
        repo, settings, documents.PRD_KIND, "", "write it", description="a prd"
    )
    assert EDIT_SENTENCE not in seen[0]


def test_revise_text_refuses_when_that_kind_has_a_checkpoint(repo, monkeypatch):
    settings = settings_with_planner(repo)
    monkeypatch.setattr(loop.agents, "run", _scripted_run(["codex:blocked#Q?"]))
    with pytest.raises(documents.DocumentQuestions):
        documents.draft(repo, settings, documents.PRD_KIND, "prd request")
    path = documents.draft_path(repo, documents.PRD_KIND)
    before = path.read_text() if path.exists() else None

    with pytest.raises(planner.PlanAlreadyInProgress):
        documents.revise_text(
            repo, settings, documents.PRD_KIND, "new text", "x", description="other"
        )
    assert (path.read_text() if path.exists() else None) == before
    assert documents.pending_description(repo, documents.PRD_KIND) == "prd request"


def test_revise_text_of_a_plan_regenerates_its_tests(repo, monkeypatch):
    settings = settings_with_planner(repo, test="codex")
    run, seen = _recording(["codex:ready", "claude:ready", "claude:approved"])
    monkeypatch.setattr(loop.agents, "run", run)

    path = documents.revise_text(
        repo, settings, documents.PLAN_KIND, "- [ ] T-1: thing\n", "split T-1",
        description="the plan", tests_text="old tests\n",
    )
    assert path.read_text() == "- [ ] T-1: thing\n"
    assert (repo / ".whyline/relay/draft-plan.tests.md").read_text() == "old tests\n"
    assert len(seen) == 3
    assert "Start from the existing draft at .whyline/relay/draft-plan.md" in seen[0]
    assert "draft-plan.tests.md" in seen[1]
    logs = sorted((repo / ".whyline" / "relay" / "logs").glob("*.log"))
    assert [p.stem.split("-")[-1] for p in logs] == ["draft", "tests", "review"]


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
