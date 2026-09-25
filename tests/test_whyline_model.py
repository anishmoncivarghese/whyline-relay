import json
from pathlib import Path

import pytest

from whyline_relay import whyline_model


def test_read_returns_empty_dict_when_absent(tmp_path: Path):
    assert whyline_model.read(tmp_path) == {}


def test_read_returns_the_file_contents(tmp_path: Path):
    target = tmp_path / ".whyline" / "model.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"codex": "gpt-5-codex", "claude": "opus"}))
    assert whyline_model.read(tmp_path) == {"codex": "gpt-5-codex", "claude": "opus"}


def test_read_corrupt_file_is_treated_as_absent(tmp_path: Path):
    target = tmp_path / ".whyline" / "model.json"
    target.parent.mkdir(parents=True)
    target.write_text("{broken")
    assert whyline_model.read(tmp_path) == {}


@pytest.mark.parametrize("content", ["[]", "null", '"string"', "123", "true"])
def test_read_malformed_top_level_value_is_treated_as_absent(tmp_path: Path, content: str):
    target = tmp_path / ".whyline" / "model.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    assert whyline_model.read(tmp_path) == {}

