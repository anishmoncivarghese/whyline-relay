from pathlib import Path

import pytest

from whyline_relay import config, setup


def _write(root: Path, text: str) -> None:
    p = root / ".whyline/relay/config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_default_release_role_is_human(tmp_path):
    assert config.load(tmp_path).release_role == "human"


@pytest.mark.parametrize("roles", [
    '[roles]\nimplementer = "codex"\nreviewer = "claude"\nrelease = "human"\n',
    '[roles]\nimplementer = "codex"\ntester = "claude"\nreviewer = "claude"\nrelease = "human"\n'
    '[pipeline]\ndefault_profile = "full"\n[pipeline.profiles]\nfull = ["draft", "review"]\n'
    '[pipeline.stages.draft]\nrole = "implementer"\nprompt = "implement"\n[pipeline.stages.draft.on]\n'
    'ready = "@next"\n[pipeline.stages.review]\nrole = "reviewer"\nprompt = "review"\n'
    '[pipeline.stages.review.on]\napproved = "@complete"\nrejected = "draft"\n',
])
def test_human_release_loads_in_both_formats(tmp_path, roles):
    _write(tmp_path, roles)
    assert config.load(tmp_path).release_role == "human"


def test_release_can_be_an_agent_but_not_nonsense(tmp_path):
    _write(tmp_path, '[roles]\nimplementer = "codex"\nreviewer = "claude"\nrelease = "grok"\n')
    assert config.load(tmp_path).release_role == "grok"
    _write(tmp_path, '[roles]\nimplementer = "codex"\nreviewer = "claude"\nrelease = "bob"\n')
    with pytest.raises(config.ConfigError, match="release"):
        config.load(tmp_path)


def test_write_release_keeps_everything_else(tmp_path):
    _write(tmp_path, 'max_rounds = 6\n[roles]\nimplementer = "codex"\nreviewer = "claude"\n')
    setup.write_release(tmp_path, "codex", commit=False)
    loaded = config.load(tmp_path)
    assert (loaded.release_role, loaded.max_rounds, loaded.roles.implementer) == ("codex", 6, "codex")


def test_write_roles_keeps_existing_release(tmp_path):
    _write(tmp_path, '[roles]\nimplementer = "codex"\nreviewer = "claude"\nrelease = "codex"\n')
    setup.write_roles(tmp_path, "antigravity", "claude", "claude", commit=False)
    loaded = config.load(tmp_path)
    assert loaded.release_role == "codex"


def test_write_roles_keeps_existing_release_in_pipeline(tmp_path):
    _write(
        tmp_path,
        '[roles]\nimplementer = "codex"\ntester = "claude"\nreviewer = "claude"\nrelease = "codex"\n'
        '[pipeline]\ndefault_profile = "full"\n[pipeline.profiles]\nfull = ["draft", "review"]\n'
        '[pipeline.stages.draft]\nrole = "implementer"\nprompt = "implement"\n[pipeline.stages.draft.on]\n'
        'ready = "@next"\n[pipeline.stages.review]\nrole = "reviewer"\nprompt = "review"\n'
        '[pipeline.stages.review.on]\napproved = "@complete"\nrejected = "draft"\n',
    )
    setup.write_roles(tmp_path, "antigravity", "claude", "claude", commit=False)
    loaded = config.load(tmp_path)
    assert loaded.release_role == "codex"
