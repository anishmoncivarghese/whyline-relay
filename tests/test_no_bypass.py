from pathlib import Path

FORBIDDEN = (
    "--dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "danger-full-access",
)

SOURCE = Path(__file__).parent.parent / "src" / "whyline_relay"


def test_no_source_file_mentions_a_bypass_flag():
    """The promise in the README, enforced by the suite rather than by memory."""
    for path in SOURCE.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for flag in FORBIDDEN:
            assert flag not in content, f"{path.name} mentions {flag}"


def test_no_source_file_runs_git_push():
    """The deny list names the ban ("Bash(git push:*)"); nothing else may mention it."""
    for path in SOURCE.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "Bash(" in line:
                continue
            assert "git push" not in line, f"{path.name}:{number}"
            assert '"push"' not in line and "'push'" not in line, f"{path.name}:{number}"
