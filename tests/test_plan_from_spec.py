import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from whyline_relay import brainstorm, loop, planner, prompts
from tests.test_planner import _scripted_run, repo, settings_with_planner  # noqa: F401


def test_plan_from_a_spec_names_it_first(repo, monkeypatch):
    spec = repo / "docs/specs/thing.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Thing\n")
    seen = []
    scripted = _scripted_run(["claude:ready", "claude:approved"])
    monkeypatch.setattr(loop.agents, "run", lambda c, p, **k: seen.append(p) or scripted(c, p, **k))
    planner.draft(repo, settings_with_planner(repo), "the thing", spec=spec)
    assert "Write the plan from this spec: docs/specs/thing.md. Read it in full first." in seen[0]


def test_plan_from_spec_outside_repo(repo, monkeypatch):
    outside = repo.parent / "outside_spec_dir"
    outside.mkdir(parents=True, exist_ok=True)
    spec = outside / "outside_spec.md"
    spec.write_text("# Outside\n")
    seen = []
    scripted = _scripted_run(["claude:ready", "claude:approved"])
    monkeypatch.setattr(loop.agents, "run", lambda c, p, **k: seen.append(p) or scripted(c, p, **k))
    planner.draft(repo, settings_with_planner(repo), "the thing", spec=spec)
    assert f"Write the plan from this spec: {str(spec)}. Read it in full first." in seen[0]


def test_plan_prompts_mark_release_tasks():
    assert "relay-profile: release" in prompts.PLAN_DRAFT
    assert "relay-profile: release" in prompts.PLAN_REVIEW


def test_final_synthesis_and_revise(repo, monkeypatch):
    doc = brainstorm.shared_path(repo, "topic")
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# Brainstorm: topic\n\n## Final Synthesis\n\nUse SQLite.\n\n## Claude\n\nx\n")
    assert brainstorm.final_synthesis(repo, "topic") == "Use SQLite."
    sent = []
    monkeypatch.setattr(brainstorm.chat, "run_turn",
                        lambda root, **kw: sent.append(kw) or {"ok": True, "agent": kw["agent"], "response": "done"})
    brainstorm.revise_synthesis(repo, settings_with_planner(repo), "claude", [("claude", "Claude")],
                                "topic", "Prefer Postgres")
    assert sent[0]["agent"] == "claude" and "Prefer Postgres" in sent[0]["prompt"]
    assert "## Final Synthesis" in sent[0]["prompt"]


def test_final_synthesis_missing_file_or_heading(repo):
    assert brainstorm.final_synthesis(repo, "nonexistent") == ""
    doc = brainstorm.shared_path(repo, "no-heading")
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# Brainstorm: no-heading\n\n## Claude\n\nx\n")
    assert brainstorm.final_synthesis(repo, "no-heading") == ""
