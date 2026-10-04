import json
from pathlib import Path

import pytest

from whyline_relay import antigravity, gitcheck


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _settings(home: Path) -> dict:
    return json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text())


def test_trust_creates_the_file_with_the_repo_and_tools(home, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert not antigravity.is_trusted(repo)
    antigravity.trust(repo)
    data = _settings(home)
    assert data["trustedWorkspaces"] == [str(repo.resolve())]
    assert data["permissions"]["allow"] == list(antigravity.ALLOW)
    assert antigravity.is_trusted(repo)


def test_trust_keeps_other_keys_and_adds_the_repo_once(home, tmp_path):
    path = home / ".gemini" / "antigravity-cli" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "theme": "dark",
        "trustedWorkspaces": ["/elsewhere"],
        "permissions": {"allow": ["read_file(*)"], "deny": ["command(rm)"]},
    }))
    repo = tmp_path / "repo"
    repo.mkdir()
    antigravity.trust(repo)
    antigravity.trust(repo)
    data = _settings(home)
    assert data["theme"] == "dark"
    assert data["trustedWorkspaces"] == ["/elsewhere", str(repo.resolve())]
    assert data["permissions"]["deny"] == ["command(rm)"]
    assert data["permissions"]["allow"] == [
        "read_file(*)", "write_file(*)", "edit_file(*)", "command(*)",
    ]


@pytest.mark.parametrize("text", ["{not json", '{"trustedWorkspaces": "x"}'])
def test_trust_refuses_a_file_it_cannot_edit_safely(home, tmp_path, text):
    path = home / ".gemini" / "antigravity-cli" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    with pytest.raises(antigravity.SettingsUnreadable):
        antigravity.trust(tmp_path)
    assert path.read_text() == text
    assert antigravity.is_trusted(tmp_path) is False


def test_decline_is_remembered_and_forgettable(tmp_path):
    assert not antigravity.is_declined(tmp_path)
    antigravity.decline(tmp_path)
    assert antigravity.is_declined(tmp_path)
    antigravity.forget_decline(tmp_path)
    assert not antigravity.is_declined(tmp_path)


def test_the_decline_file_is_git_ignored():
    assert ".whyline/relay/antigravity-declined" in gitcheck.RELAY_IGNORE
