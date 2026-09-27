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
        ("claude", "Claude"), ("agy", "Antigravity"),
    ]


def test_parse_model_selection_five_means_all():
    assert brainstorm.parse_model_selection("5") == [
        ("claude", "Claude"), ("codex", "Codex"),
        ("agy", "Antigravity"), ("grok", "Grok"),
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
    answers = iter(["caching strategy", "1,2", "2", "claude"])
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
    }


def test_ask_brainstorm_setup_rejects_empty_topic_then_accepts(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["", "real topic", "1", "0", "claude"])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["topic"] == "real topic"
    assert result["passes"] == 0


def test_ask_brainstorm_setup_defaults_passes_to_one(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["topic", "1", "", "claude"])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["passes"] == 1


def test_ask_brainstorm_setup_rejects_a_final_model_not_selected(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["topic", "1", "1", "codex", "claude"])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["final_agent"] == "claude"


def test_ask_brainstorm_setup_declines_and_aborts_on_unavailable_model(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["topic", "1,4", "1", "claude", "n"])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result is None


def test_ask_brainstorm_setup_proceeds_without_an_unavailable_model(tmp_path: Path):
    settings = config.load(tmp_path)
    answers = iter(["topic", "1,4", "1", "claude", "y"])
    result = brainstorm.ask_brainstorm_setup(
        tmp_path, settings,
        input_fn=lambda prompt="": next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert result["models"] == [("claude", "Claude")]
