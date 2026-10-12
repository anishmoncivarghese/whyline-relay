import json
from pathlib import Path

from whyline_relay import brainstorm, chat, config, recipes


def test_slugify_lowercases_and_hyphenates():
    assert brainstorm.slugify("Best Caching Strategy!") == "best-caching-strategy"


def test_slugify_truncates_long_topics():
    long_topic = "x" * 200
    assert len(brainstorm.slugify(long_topic)) == 60


def test_slugify_never_returns_empty():
    assert brainstorm.slugify("!!!") == "topic"


def test_parse_model_selection_comma_separated():
    assert brainstorm.parse_model_selection("1,3") == [
        ("claude", "Claude"), ("antigravity", "Antigravity"),
    ]


def test_parse_model_selection_five_means_all():
    assert brainstorm.parse_model_selection("5") == [
        ("claude", "Claude"), ("codex", "Codex"),
        ("antigravity", "Antigravity"), ("grok", "Grok"),
    ]


def test_parse_model_selection_rejects_garbage():
    assert brainstorm.parse_model_selection("abc") is None
    assert brainstorm.parse_model_selection("6") is None
    assert brainstorm.parse_model_selection("") is None


def test_parse_model_selection_deduplicates():
    assert brainstorm.parse_model_selection("1,1,2") == [
        ("claude", "Claude"), ("codex", "Codex"),
    ]


def test_check_availability_reports_an_unconfigured_agent(tmp_path: Path):
    settings = config.load(tmp_path)
    unavailable = brainstorm.check_availability(
        settings, [("claude", "Claude"), ("custom", "Custom")]
    )
    assert unavailable == [("custom", "Custom")]


def test_check_availability_empty_when_all_configured(tmp_path: Path):
    settings = config.load(tmp_path)
    unavailable = brainstorm.check_availability(
        settings, [("claude", "Claude"), ("codex", "Codex")]
    )
    assert unavailable == []


def test_ask_brainstorm_setup_happy_path(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["caching strategy", "1,2", "2", "claude", ""])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result == {
        "topic": "caching strategy",
        "models": [("claude", "Claude"), ("codex", "Codex")],
        "passes": 2,
        "final_agent": "claude",
        "timeout_seconds": 900,
        "timeout_minutes": 15,
    }


def test_ask_brainstorm_setup_rejects_empty_topic_then_accepts(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["", "real topic", "1", "0", "claude", ""])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["topic"] == "real topic"
    assert result["passes"] == 0
    assert result["timeout_seconds"] == 900
    assert result["timeout_minutes"] == 15


def test_ask_brainstorm_setup_defaults_passes_to_one(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["topic", "1", "", "claude", ""])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["passes"] == 1
    assert result["timeout_seconds"] == 900


def test_ask_brainstorm_setup_rejects_a_final_model_not_selected(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["topic", "1", "1", "codex", "claude", ""])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["final_agent"] == "claude"
    assert result["timeout_seconds"] == 900


def test_ask_brainstorm_setup_declines_and_aborts_on_unavailable_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(recipes, "RECIPES", {})
    settings = config.load(tmp_path)
    answers = iter(["topic", "1,4", "1", "claude", "", "n"])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result is None


def test_ask_brainstorm_setup_proceeds_without_an_unavailable_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(recipes, "RECIPES", {})
    settings = config.load(tmp_path)
    answers = iter(["topic", "1,4", "1", "claude", "", "y"])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["models"] == [("claude", "Claude")]
    assert result["timeout_seconds"] == 900


def test_parse_timeout_selection_valid_inputs():
    assert brainstorm.parse_timeout_selection("") == 15
    assert brainstorm.parse_timeout_selection("  ") == 15
    assert brainstorm.parse_timeout_selection("15") == 15
    assert brainstorm.parse_timeout_selection("30") == 30
    assert brainstorm.parse_timeout_selection("45") == 45
    assert brainstorm.parse_timeout_selection("60") == 60
    # Shorthand menu numbers
    assert brainstorm.parse_timeout_selection("1") == 15
    assert brainstorm.parse_timeout_selection("2") == 30
    assert brainstorm.parse_timeout_selection("3") == 45
    assert brainstorm.parse_timeout_selection("4") == 60
    # Units
    assert brainstorm.parse_timeout_selection("15m") == 15
    assert brainstorm.parse_timeout_selection("30 min") == 30
    assert brainstorm.parse_timeout_selection("45 minutes") == 45


def test_parse_timeout_selection_invalid_inputs():
    assert brainstorm.parse_timeout_selection("10") is None
    assert brainstorm.parse_timeout_selection("20") is None
    assert brainstorm.parse_timeout_selection("90") is None
    assert brainstorm.parse_timeout_selection("abc") is None
    assert brainstorm.parse_timeout_selection("-15") is None
    assert brainstorm.parse_timeout_selection("5") is None


def test_parse_timeout_selection_unlimited_and_empty_default():
    assert brainstorm.parse_timeout_selection("none") is chat.NO_LIMIT
    assert brainstorm.parse_timeout_selection("No Limit") is chat.NO_LIMIT
    assert brainstorm.parse_timeout_selection("  unlimited  ") is chat.NO_LIMIT
    assert brainstorm.parse_timeout_selection("0") is chat.NO_LIMIT
    assert brainstorm.parse_timeout_selection("") == 15
    assert brainstorm.parse_timeout_selection("   ") == 15
    assert brainstorm.parse_timeout_selection("", default=30) == 30
    assert brainstorm.parse_timeout_selection("", default=chat.NO_LIMIT) is chat.NO_LIMIT
    assert brainstorm.parse_timeout_selection("none") is not None


def test_ask_brainstorm_setup_explicit_timeout_choices(tmp_path: Path):
    settings = config.load(tmp_path)
    for choice, expected_min, expected_sec in [
        ("15", 15, 900),
        ("30", 30, 1800),
        ("45", 45, 2700),
        ("60", 60, 3600),
    ]:
        answers = iter(["topic", "1", "1", "claude", choice])
        result = brainstorm.ask_brainstorm_setup(
            tmp_path, settings,
            input_fn=lambda prompt="": next(answers),
            print_fn=lambda *a, **k: None,
        )
        assert result["timeout_minutes"] == expected_min
        assert result["timeout_seconds"] == expected_sec


def test_ask_brainstorm_setup_timeout_validation_rejects_invalid_then_accepts(tmp_path: Path):
    settings = config.load(tmp_path)
    printed = []
    # Two invalid inputs ("foo", "20"), then valid "45"
    answers = iter(["topic", "1", "1", "claude", "foo", "20", "45"])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    assert result["timeout_minutes"] == 45
    assert result["timeout_seconds"] == 2700
    assert sum("Enter a valid timeout" in line for line in printed) == 2


def test_ask_brainstorm_setup_persists_timeout_for_resumed_run(tmp_path: Path):
    settings = config.load(tmp_path)
    # 1. First run selects 45 minutes explicitly
    answers1 = iter(["resumed topic", "1", "1", "claude", "45"])
    result1 = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers1),
        print_fn=lambda *a, **k: None,
    )
    assert result1["timeout_minutes"] == 45
    assert result1["timeout_seconds"] == 2700

    # Verify file was written to disk
    timeout_file = brainstorm.timeout_path(tmp_path, "resumed topic")
    assert timeout_file.exists()
    assert brainstorm.load_timeout(tmp_path, "resumed topic") == 2700

    # 2. Resumed run on same topic hits enter (default), which should pick 45
    answers2 = iter(["resumed topic", "1", "1", "claude", ""])
    prompts_seen = []

    def recording_input(prompt=""):
        prompts_seen.append(prompt)
        return next(answers2)

    result2 = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=recording_input,
        print_fn=lambda *a, **k: None,
    )
    assert result2["timeout_minutes"] == 45
    assert result2["timeout_seconds"] == 2700
    assert any("[45]: " in p for p in prompts_seen)


def _timeout_prompt(tmp_path: Path, topic: str, raw_timeout: str) -> tuple[dict, list[str]]:
    settings = config.load(tmp_path)
    prompts: list[str] = []
    answers = iter([topic, "1", "1", "claude", raw_timeout])

    def record(prompt=""):
        prompts.append(prompt)
        return next(answers)

    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings, input_fn=record, print_fn=lambda *a, **k: None,
    )
    return result, [prompt for prompt in prompts if prompt.startswith("Per-agent timeout:")]


def test_timeout_prompt_defaults_to_15_when_missing(tmp_path: Path):
    result, prompts = _timeout_prompt(tmp_path, "fresh topic", "")
    assert prompts == [
        "Per-agent timeout: 15 (default), 30, 45, 60 minutes, or none [15]: "
    ]
    assert result["timeout_seconds"] == 900
    assert result["timeout_minutes"] == 15


def test_timeout_prompt_preselects_none_for_the_sentinel(tmp_path: Path):
    brainstorm.save_timeout(tmp_path, chat.NO_LIMIT, "saved topic")
    result, prompts = _timeout_prompt(tmp_path, "saved topic", "")
    assert prompts == [
        "Per-agent timeout: 15 (default), 30, 45, 60 minutes, or none [none]: "
    ]
    assert result["timeout_seconds"] is chat.NO_LIMIT
    assert result["timeout_minutes"] is None
    payload = json.loads(brainstorm.timeout_path(tmp_path, "saved topic").read_text(encoding="utf-8"))
    assert payload == {"timeout_seconds": None}


def test_timeout_prompt_outside_menu_preselects_15(tmp_path: Path):
    path = brainstorm.timeout_path(tmp_path, "odd topic")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"timeout_seconds": 300}), encoding="utf-8")
    result, prompts = _timeout_prompt(tmp_path, "odd topic", "")
    assert prompts == [
        "Per-agent timeout: 15 (default), 30, 45, 60 minutes, or none [15]: "
    ]
    assert result["timeout_seconds"] == 900
    assert result["timeout_minutes"] == 15


def test_ask_brainstorm_setup_none_stores_null_without_dividing(tmp_path: Path):
    settings = config.load(tmp_path)
    for choice in ("none", "no limit", "unlimited", "0"):
        answers = iter(["unlimited topic", "1", "1", "claude", choice])
        result = brainstorm.ask_brainstorm_setup(
            tmp_path, settings,
            input_fn=lambda prompt="": next(answers),
            print_fn=lambda *a, **k: None,
        )
        assert result["timeout_seconds"] is chat.NO_LIMIT
        assert result["timeout_minutes"] is None
        payload = json.loads(
            brainstorm.timeout_path(tmp_path, "unlimited topic").read_text(encoding="utf-8")
        )
        assert payload == {"timeout_seconds": None}
        assert "timeout_minutes" not in payload


_UNATTENDED_SYNTHESIS_PARAGRAPH = (
    "Decide questions whose answer follows from the request, "
    "record each decision in the document, and leave "
    '"## Open questions" only for a choice that would change what '
    "gets built and cannot be derived from the request."
)


def _expected_synthesis_prompt(root: Path) -> str:
    shared = brainstorm.shared_path(root, "topic")
    return (
        'All review passes are complete for this brainstorm on "topic". '
        f'Read {shared} in full and write a new "## Final Synthesis" '
        "section (at the top, right after the title) combining the "
        "strongest ideas from every model's section into one clear, "
        "actionable recommendation."
    )


def _capture_synthesis_prompt(tmp_path: Path, monkeypatch, **kwargs) -> str:
    settings = config.load(tmp_path)
    shared = brainstorm.shared_path(tmp_path, "topic")
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(
        "# Brainstorm: topic\n\n## Codex\n\nresearch\n", encoding="utf-8"
    )
    captured: dict[str, str] = {}

    def fake_run_turn(root, *, agent, prompt, settings, exclude=frozenset(), **kw):
        captured["prompt"] = prompt
        return {"agent": agent, "response": "ok", "rate_limited": False, "ok": True}

    monkeypatch.setattr(brainstorm.chat, "run_turn", fake_run_turn)
    brainstorm.run_final_synthesis(
        tmp_path, "codex", [("codex", "Codex")], "topic",
        settings=settings, **kwargs,
    )
    return captured["prompt"]


def test_run_final_synthesis_unattended_false_keeps_the_prompt(tmp_path: Path, monkeypatch):
    expected = _expected_synthesis_prompt(tmp_path)
    assert _capture_synthesis_prompt(tmp_path, monkeypatch) == expected
    assert _capture_synthesis_prompt(tmp_path, monkeypatch, unattended=False) == expected
    assert _UNATTENDED_SYNTHESIS_PARAGRAPH not in expected


def test_run_final_synthesis_unattended_true_appends_only_that_paragraph(
    tmp_path: Path, monkeypatch
):
    expected = _expected_synthesis_prompt(tmp_path)
    prompt = _capture_synthesis_prompt(tmp_path, monkeypatch, unattended=True)
    assert prompt == expected + "\n\n" + _UNATTENDED_SYNTHESIS_PARAGRAPH
    assert _UNATTENDED_SYNTHESIS_PARAGRAPH not in brainstorm.PLAN_GENERATION_PROMPT
