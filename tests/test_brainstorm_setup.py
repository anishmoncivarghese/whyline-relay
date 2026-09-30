from pathlib import Path

from whyline_relay import brainstorm, config


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
        settings, [("claude", "Claude"), ("grok", "Grok")]
    )
    assert unavailable == [("grok", "Grok")]


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


def test_ask_brainstorm_setup_declines_and_aborts_on_unavailable_model(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["topic", "1,4", "1", "claude", "", "n"])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result is None


def test_ask_brainstorm_setup_proceeds_without_an_unavailable_model(tmp_path: Path):
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
    assert brainstorm.parse_timeout_selection("0") is None
    assert brainstorm.parse_timeout_selection("10") is None
    assert brainstorm.parse_timeout_selection("20") is None
    assert brainstorm.parse_timeout_selection("90") is None
    assert brainstorm.parse_timeout_selection("abc") is None
    assert brainstorm.parse_timeout_selection("-15") is None


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
