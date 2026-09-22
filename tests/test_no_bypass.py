import tomllib
from pathlib import Path

from whyline_relay import adapters, config, init
from whyline_relay.adapters import bypass

FORBIDDEN = (
    "--dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "danger-full-access",
    "bypassPermissions",
)

SOURCE = Path(__file__).parent.parent / "src" / "whyline_relay"
EXEMPT = {"adapters/bypass.py"}


def test_no_source_file_mentions_a_bypass_flag():
    """The promise in the README, enforced by the suite rather than by memory."""
    for path in SOURCE.rglob("*.py"):
        if path.relative_to(SOURCE).as_posix() in EXEMPT:
            continue
        content = path.read_text(encoding="utf-8")
        for flag in FORBIDDEN:
            assert flag not in content, f"{path.name} mentions {flag}"


def test_only_bypass_py_is_exempt():
    assert EXEMPT == {"adapters/bypass.py"}


def test_no_default_command_or_generated_config_carries_a_bypass_flag(tmp_path):
    for name, adapter in adapters.BUILTIN.items():
        assert bypass.find(name, adapter.default_command) == []
    for name, command in config.DEFAULTS["agents"].items():
        assert bypass.find(name, command) == []

    for implementer in adapters.BUILTIN:
        for reviewer in adapters.BUILTIN:
            root = tmp_path / f"{implementer}-{reviewer}"
            root.mkdir()
            assert init.run(
                root,
                assume_yes=True,
                implementer=implementer,
                reviewer=reviewer,
            ) == 0
            raw = tomllib.loads(config.config_path(root).read_text(encoding="utf-8"))
            for name, table in raw["agents"].items():
                assert bypass.find(name, table["command"]) == []


def test_no_source_file_runs_git_push():
    """The deny list names the ban ("Bash(git push:*)"); nothing else may mention it."""
    for path in SOURCE.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "Bash(" in line:
                continue
            assert "git push" not in line, f"{path.name}:{number}"
            assert '"push"' not in line and "'push'" not in line, f"{path.name}:{number}"
