import subprocess
from pathlib import Path

from whyline_relay import brainstorm, chat, config


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def test_run_review_pass_invokes_every_model_once(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        calls.append(command[0])
        return RunResult(0, '{"type":"result","result":"revised"}\n')

    brainstorm.run_review_pass(
        tmp_path, models, "my topic", 1, settings=settings, run_fn=fake_run_fn,
        print_fn=lambda *a, **k: None,
    )
    assert calls == ["claude", "codex"]


def test_run_review_pass_skips_a_model_that_times_out(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay import agents
        from whyline_relay.agents import RunResult
        calls.append(command[0])
        if command[0] == "claude":
            raise agents.AgentTimeout("claude exceeded 300s")
        return RunResult(0, '{"type":"result","result":"revised"}\n')

    printed = []
    brainstorm.run_review_pass(
        tmp_path, models, "my topic", 1, settings=settings, run_fn=fake_run_fn,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    assert calls == ["claude", "codex"]  # codex still ran despite claude's timeout
    assert any("claude" in line for line in printed)


def test_run_review_pass_commit_message_names_the_pass_and_topic(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude")]
    seen = {}

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        return RunResult(0, '{"type":"result","result":"revised"}\n')

    real_run_turn = brainstorm.chat.run_turn

    def spying_run_turn(*args, **kwargs):
        seen["commit_message"] = kwargs.get("commit_message")
        return real_run_turn(*args, **kwargs)

    monkeypatch.setattr(brainstorm.chat, "run_turn", spying_run_turn)
    brainstorm.run_review_pass(
        tmp_path, models, "my topic", 2, settings=settings,
        run_fn=fake_run_fn, print_fn=lambda *a, **k: None,
    )
    assert seen["commit_message"] == 'brainstorm: claude review pass 2 on "my topic"'


def test_run_final_synthesis_only_invokes_the_designated_model(tmp_path: Path):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    calls = []

    def fake_run_fn(command, prompt, **kwargs):
        from whyline_relay.agents import RunResult
        calls.append(command[0])
        # Codex's adapter sets uses_output_file, so run_turn reads the
        # response from `-o` and ignores RunResult.output. The file is the
        # last message as plain text; Claude's JSON envelope would come
        # back unparsed.
        text = "final answer\n"
        if "-o" in command:
            Path(command[command.index("-o") + 1]).write_text(text, encoding="utf-8")
        return RunResult(0, text)

    record = brainstorm.run_final_synthesis(
        tmp_path, "codex", models, "my topic", settings=settings, run_fn=fake_run_fn
    )
    assert calls == ["codex"]
    assert record["response"] == "final answer"
    assert record["agent"] == "codex"


def test_run_pass_zero_excludes_other_selected_models(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    captured_excludes = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        captured_excludes.append((agent, exclude))
        return {"agent": agent, "response": "ok", "rate_limited": False, "ok": True}

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    brainstorm.run_pass_zero(tmp_path, models, "topic", settings=settings)
    excludes_by_agent = dict(captured_excludes)
    assert excludes_by_agent["claude"] == frozenset({"codex"})
    assert excludes_by_agent["codex"] == frozenset({"claude"})


def test_merge_pass_zero_labels_a_substituted_agent_by_who_actually_ran(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        # claude's slot is actually served by codex (a chain substitution)
        actual = "codex" if agent == "claude" else agent
        brainstorm.temp_path(root, agent).parent.mkdir(parents=True, exist_ok=True)
        brainstorm.temp_path(root, agent).write_text(f"findings from {actual}\n")
        return {"agent": actual, "response": "ok", "rate_limited": False, "ok": True}

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    brainstorm.run_pass_zero(tmp_path, models, "topic", settings=settings)
    brainstorm.merge_pass_zero(tmp_path, models, "topic")
    shared = brainstorm.shared_path(tmp_path, "topic").read_text(encoding="utf-8")
    assert "## Codex" in shared
    # claude's own slot was actually served by codex, so it must not also
    # appear mislabeled as "## Claude"
    assert shared.count("## Codex") == 2


def test_run_review_pass_excludes_other_selected_models(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    captured_excludes = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        captured_excludes.append((agent, exclude))
        return {"agent": agent, "response": "ok", "rate_limited": False, "ok": True}

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    brainstorm.run_review_pass(
        tmp_path, models, "topic", 1, settings=settings, print_fn=lambda *a, **k: None
    )
    excludes_by_agent = dict(captured_excludes)
    assert excludes_by_agent["claude"] == frozenset({"codex"})
    assert excludes_by_agent["codex"] == frozenset({"claude"})


def test_run_final_synthesis_excludes_other_selected_models(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    captured_excludes = []

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        captured_excludes.append((agent, exclude))
        return {"agent": agent, "response": "ok", "rate_limited": False, "ok": True}

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    brainstorm.run_final_synthesis(tmp_path, "codex", models, "topic", settings=settings)
    assert len(captured_excludes) == 1
    assert captured_excludes[0] == ("codex", frozenset({"claude"}))


def test_merge_pass_zero_accepts_explicit_actual_agents(tmp_path: Path):
    _init_repo(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]
    brainstorm.temp_path(tmp_path, "claude").parent.mkdir(parents=True, exist_ok=True)
    brainstorm.temp_path(tmp_path, "claude").write_text("findings from codex\n")
    brainstorm.temp_path(tmp_path, "codex").write_text("findings from codex\n")
    brainstorm.merge_pass_zero(
        tmp_path, models, "topic", actual_agents={"claude": "codex", "codex": "codex"}
    )
    shared = brainstorm.shared_path(tmp_path, "topic").read_text(encoding="utf-8")
    assert "## Codex" in shared
    assert shared.count("## Codex") == 2


def test_run_review_pass_relabels_section_on_substitution(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude")]
    shared = brainstorm.shared_path(tmp_path, "topic")
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text("# Brainstorm: topic\n\n## Claude\n\noriginal\n", encoding="utf-8")

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        return {"agent": "codex", "response": "ok", "rate_limited": False, "ok": True}

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    results = brainstorm.run_review_pass(
        tmp_path, models, "topic", 1, settings=settings, print_fn=lambda *a, **k: None
    )
    assert results == {"claude": "codex"}
    content = shared.read_text(encoding="utf-8")
    assert "## Codex" in content
    assert "## Claude" not in content


def test_sequential_brainstorm_flow_with_substitutions_and_multi_review(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("agy", "Antigravity")]
    topic = "caching design"

    captured_prompts = []
    current_phase = {"phase": 0}

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        captured_prompts.append((current_phase["phase"], agent, prompt, exclude))
        phase = current_phase["phase"]
        if phase == 0:
            actual = "codex" if agent == "claude" else "agy"
            tpath = brainstorm.temp_path(root, agent)
            tpath.parent.mkdir(parents=True, exist_ok=True)
            tpath.write_text(f"research from {actual}\n", encoding="utf-8")
        elif phase == 1:
            actual = "grok" if agent == "claude" else "agy"
        else:
            actual = "grok" if agent == "claude" else "agy"
        return {
            "agent": actual,
            "response": f"response from {actual}",
            "ok": True,
            "rate_limited": False,
        }

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)

    # 1. Pass zero: claude substituted by codex
    current_phase["phase"] = 0
    p0_map = brainstorm.run_pass_zero(tmp_path, models, topic, settings=settings)
    assert p0_map == {"claude": "codex", "agy": "agy"}

    # 2. Merge pass zero without passing actual_agents explicitly (tests cache loading and preservation)
    brainstorm.merge_pass_zero(tmp_path, models, topic)
    shared = brainstorm.shared_path(tmp_path, topic).read_text(encoding="utf-8")
    assert "## Codex" in shared
    assert "## Antigravity" in shared
    assert "## Claude" not in shared

    # 3. Review pass 1: prompt targets ## Codex (actual agent from pass 0)
    current_phase["phase"] = 1
    p1_map = brainstorm.run_review_pass(
        tmp_path, models, topic, 1, settings=settings, print_fn=lambda *a, **k: None
    )
    p1_prompts = {agent: prompt for ph, agent, prompt, _ in captured_prompts if ph == 1}
    assert '("## Codex")' in p1_prompts["claude"]
    assert '("## Antigravity")' in p1_prompts["agy"]
    assert '("## Claude")' not in p1_prompts["claude"]

    # In review pass 1, claude slot substituted to grok; verify section was relabeled
    shared_after_p1 = brainstorm.shared_path(tmp_path, topic).read_text(encoding="utf-8")
    assert "## Grok" in shared_after_p1
    assert "## Codex" not in shared_after_p1
    assert p1_map == {"claude": "grok", "agy": "agy"}

    # 4. Review pass 2 (multi-review propagation): prompt for claude slot now targets ## Grok
    current_phase["phase"] = 2
    p2_map = brainstorm.run_review_pass(
        tmp_path, models, topic, 2, settings=settings, print_fn=lambda *a, **k: None
    )
    p2_prompts = {agent: prompt for ph, agent, prompt, _ in captured_prompts if ph == 2}
    assert '("## Grok")' in p2_prompts["claude"]
    assert '("## Codex")' not in p2_prompts["claude"]
    assert '("## Claude")' not in p2_prompts["claude"]
    assert '("## Antigravity")' in p2_prompts["agy"]
    assert p2_map == {"claude": "grok", "agy": "agy"}


def test_repl_brainstorm_wires_actual_agents_through_reviews(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    chat.save_default_agent(tmp_path, "claude")

    answers = iter([
        "/brainstorm",
        "distributed sync",  # topic
        "1,2",  # models: claude, codex
        "2",  # 2 passes
        "codex",  # final synthesis
        "/exit",
    ])

    captured_prompts = []
    turn_counter = {"count": 0}

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        turn_counter["count"] += 1
        cnt = turn_counter["count"]
        captured_prompts.append((cnt, agent, prompt, exclude))
        if cnt == 1:
            # claude pass 0 -> substituted by agy
            tpath = brainstorm.temp_path(root, agent)
            tpath.parent.mkdir(parents=True, exist_ok=True)
            tpath.write_text("claude slot findings from agy\n", encoding="utf-8")
            return {"agent": "agy", "response": "ok", "rate_limited": False, "ok": True}
        elif cnt == 2:
            # codex pass 0 -> codex
            tpath = brainstorm.temp_path(root, agent)
            tpath.parent.mkdir(parents=True, exist_ok=True)
            tpath.write_text("codex findings\n", encoding="utf-8")
            return {"agent": "codex", "response": "ok", "rate_limited": False, "ok": True}
        elif cnt == 3:
            # claude pass 1 review -> substituted by grok
            return {"agent": "grok", "response": "revised by grok", "rate_limited": False, "ok": True}
        elif cnt == 4:
            # codex pass 1 review -> codex
            return {"agent": "codex", "response": "revised by codex", "rate_limited": False, "ok": True}
        elif cnt == 5:
            # claude pass 2 review -> grok
            return {"agent": "grok", "response": "revised again by grok", "rate_limited": False, "ok": True}
        elif cnt == 6:
            # codex pass 2 review -> codex
            return {"agent": "codex", "response": "revised again by codex", "rate_limited": False, "ok": True}
        else:
            # final synthesis -> codex
            return {"agent": "codex", "response": "final synthesis text", "rate_limited": False, "ok": True}

    monkeypatch.setattr(chat, "run_turn", fake_run_turn)

    printed = []
    chat.repl(
        tmp_path,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        which=lambda name: "/bin/x",
    )

    # Check prompts
    # Turn 3 (claude pass 1 review): prompt should target "## Antigravity"
    turn3_prompt = captured_prompts[2][2]
    assert '("## Antigravity")' in turn3_prompt
    assert '("## Claude")' not in turn3_prompt

    # Turn 5 (claude pass 2 review): prompt should target "## Grok"
    turn5_prompt = captured_prompts[4][2]
    assert '("## Grok")' in turn5_prompt
    assert '("## Antigravity")' not in turn5_prompt
    assert '("## Claude")' not in turn5_prompt

    shared = brainstorm.shared_path(tmp_path, "distributed sync").read_text(encoding="utf-8")
    assert "## Grok" in shared
    assert "## Codex" in shared
    assert "## Claude" not in shared
    assert any("final synthesis text" in line for line in printed)


def test_unrelated_topic_cannot_load_another_topics_actual_agent_mapping(
    tmp_path: Path, monkeypatch
):
    _init_repo(tmp_path)
    settings = config.load(tmp_path)
    models = [("claude", "Claude"), ("codex", "Codex")]

    # 1. Run pass 0 on "topic-alpha" where claude is substituted by grok
    def fake_run_turn_alpha(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        actual = "grok" if agent == "claude" else "codex"
        tpath = brainstorm.temp_path(root, agent)
        tpath.parent.mkdir(parents=True, exist_ok=True)
        tpath.write_text(f"research from {actual}\n", encoding="utf-8")
        return {"agent": actual, "response": "ok", "ok": True, "rate_limited": False}

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn_alpha)
    brainstorm.run_pass_zero(tmp_path, models, "topic-alpha", settings=settings)

    # Verify topic-alpha wrote its topic-scoped mapping
    alpha_map = brainstorm._load_actual_agents(tmp_path, "topic-alpha")
    assert alpha_map == {"claude": "grok", "codex": "codex"}

    # Also plant a stale unscoped .actual-agents.json to ensure it is never loaded
    unscoped = config.relay_dir(tmp_path) / "brainstorm-tmp" / ".actual-agents.json"
    unscoped.parent.mkdir(parents=True, exist_ok=True)
    unscoped.write_text('{"claude": "stale_agent"}', encoding="utf-8")

    # 2. Check an unrelated topic "topic-beta" before it has run
    beta_map = brainstorm._load_actual_agents(tmp_path, "topic-beta")
    assert beta_map == {}, "unrelated topic must not load another topic's mapping or unscoped file"

    # 3. If merge_pass_zero is run on topic-beta without explicit actual_agents,
    # it must attribute claude to Claude, NOT grok (from topic-alpha) and NOT stale_agent
    brainstorm.temp_path(tmp_path, "claude").write_text("claude own text\n", encoding="utf-8")
    brainstorm.merge_pass_zero(tmp_path, [("claude", "Claude")], "topic-beta")
    shared_beta = brainstorm.shared_path(tmp_path, "topic-beta").read_text(encoding="utf-8")
    assert "## Claude" in shared_beta
    assert "## Grok" not in shared_beta
    assert "## stale_agent" not in shared_beta

    # 4. If run_review_pass is run on topic-beta without explicit actual_agents,
    # its prompt targets "## Claude", not "## Grok"
    captured_prompts = []

    def fake_run_turn_beta(root, *, agent, prompt, settings, exclude=frozenset(), **kwargs):
        captured_prompts.append((agent, prompt))
        return {"agent": agent, "response": "ok", "ok": True, "rate_limited": False}

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn_beta)
    brainstorm.run_review_pass(
        tmp_path, [("claude", "Claude")], "topic-beta", 1, settings=settings, print_fn=lambda *a, **k: None
    )
    assert len(captured_prompts) == 1
    assert '("## Claude")' in captured_prompts[0][1]
    assert '("## Grok")' not in captured_prompts[0][1]
