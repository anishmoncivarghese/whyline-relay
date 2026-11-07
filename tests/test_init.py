import json
import os
import re
import subprocess
from pathlib import Path

from whyline_relay import cli, init


# .gitignore is created once by ensure_relay_gitignore. It is not reported
# or replaced by the generated-file loop, including under --overwrite.
GENERATED = (
    ".whyline/relay/claude-settings.json",
    ".whyline/relay/prompts/implement.md",
    ".whyline/relay/prompts/review.md",
    ".whyline/relay/config.toml",
)


def generated_paths(root: Path) -> list[Path]:
    return [root / relative for relative in GENERATED]


def test_detects_python_from_pyproject(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert init.detect_stack(tmp_path) == "python"


def test_detects_node_from_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    assert init.detect_stack(tmp_path) == "node"


def test_unknown_stack_falls_back_to_base(tmp_path: Path):
    assert init.detect_stack(tmp_path) == "base"


def test_allowlist_denies_push_and_recursive_delete():
    built = init.allowlist("python")
    assert "Bash(git push:*)" in built["permissions"]["deny"]
    assert "Bash(rm -rf:*)" in built["permissions"]["deny"]


def test_allowlist_never_contains_a_bypass_flag():
    for stack in ("python", "node", "base"):
        assert "dangerously" not in json.dumps(init.allowlist(stack))


def test_python_preset_allows_pytest():
    allowed = init.allowlist("python")["permissions"]["allow"]
    assert "Bash(pytest:*)" in allowed
    assert "Bash(uv run pytest:*)" in allowed
    assert "Bash(.venv/bin/pytest:*)" in allowed
    assert "Bash(.venv/bin/python:*)" in allowed


def test_base_allowlist_allows_read_only_shell_helpers():
    allowed = init.allowlist("base")["permissions"]["allow"]
    assert "Bash(tail:*)" in allowed
    assert "Bash(head:*)" in allowed
    assert "Bash(wc:*)" in allowed
    assert "Bash(grep:*)" in allowed
    assert "Bash(ls:*)" in allowed


def test_node_preset_allows_npm_test():
    assert "Bash(npm test:*)" in init.allowlist("node")["permissions"]["allow"]


def test_run_writes_settings_templates_and_config(tmp_path: Path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert init.run(tmp_path, assume_yes=True) == 0
    out = capsys.readouterr().out
    settings = json.loads(
        (tmp_path / ".whyline" / "relay" / "claude-settings.json").read_text()
    )
    assert "Bash(whyline:*)" in settings["permissions"]["allow"]
    assert (tmp_path / ".whyline" / "relay" / "prompts" / "implement.md").exists()
    assert (tmp_path / ".whyline" / "relay" / "prompts" / "review.md").exists()
    assert (tmp_path / ".whyline" / "relay" / "config.toml").exists()
    assert (tmp_path / ".whyline" / "relay" / ".gitignore").read_text() == (
        "\n".join(init.RELAY_GITIGNORE_LINES) + "\n"
    )
    for relative in GENERATED:
        assert f"Wrote {relative}." in out


def test_second_run_with_no_edits_does_not_touch_or_report_files(
    tmp_path: Path, capsys
):
    init.run(tmp_path, assume_yes=True)
    capsys.readouterr()
    old_timestamp = 1_000_000_000
    gitignore = tmp_path / ".whyline" / "relay" / ".gitignore"
    for path in (*generated_paths(tmp_path), gitignore):
        os.utime(path, ns=(old_timestamp, old_timestamp))

    assert init.run(tmp_path, assume_yes=True) == 0

    out = capsys.readouterr().out
    assert "Kept " not in out
    assert "Wrote " not in out
    assert all(
        path.stat().st_mtime_ns == old_timestamp
        for path in (*generated_paths(tmp_path), gitignore)
    )


def test_an_edited_config_is_kept_and_reported(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    capsys.readouterr()
    config_path = tmp_path / ".whyline" / "relay" / "config.toml"
    config_path.write_text("edited config\n")

    assert init.run(tmp_path, assume_yes=True) == 0

    assert config_path.read_text() == "edited config\n"
    assert capsys.readouterr().out.count(
        "Kept .whyline/relay/config.toml: it already exists and differs. "
        "Run with --overwrite to replace it.\n"
    ) == 1


def test_edited_prompt_and_permissions_are_kept_and_reported(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    capsys.readouterr()
    edited = {
        tmp_path / ".whyline" / "relay" / "prompts" / "implement.md": "my prompt\n",
        tmp_path / ".whyline" / "relay" / "claude-settings.json": "my permissions\n",
    }
    for path, content in edited.items():
        path.write_text(content)

    assert init.run(tmp_path, assume_yes=True) == 0

    out = capsys.readouterr().out
    for path, content in edited.items():
        assert path.read_text() == content
        relative = path.relative_to(tmp_path)
        assert (
            f"Kept {relative}: it already exists and differs. "
            "Run with --overwrite to replace it."
        ) in out


def test_overwrite_restores_edits_and_rewrites_every_file(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    defaults = {path: path.read_bytes() for path in generated_paths(tmp_path)}
    capsys.readouterr()
    for path in generated_paths(tmp_path)[:3]:
        path.write_text("edited\n")
    old_timestamp = 1_000_000_000
    gitignore = tmp_path / ".whyline" / "relay" / ".gitignore"
    gitignore_bytes = gitignore.read_bytes()
    for path in (*generated_paths(tmp_path), gitignore):
        os.utime(path, ns=(old_timestamp, old_timestamp))

    assert (
        cli.main(["init", "--repo", str(tmp_path), "--yes", "--overwrite"])
        == cli.EXIT_OK
    )

    out = capsys.readouterr().out
    for path, content in defaults.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns != old_timestamp
        assert f"Wrote {path.relative_to(tmp_path)}." in out
    assert "Kept " not in out
    assert gitignore.read_bytes() == gitignore_bytes
    assert gitignore.stat().st_mtime_ns == old_timestamp
    assert "Wrote .whyline/relay/.gitignore." not in out


def test_a_missing_file_is_created_among_existing_files(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    capsys.readouterr()
    missing = tmp_path / ".whyline" / "relay" / "prompts" / "review.md"
    missing.unlink()

    assert init.run(tmp_path, assume_yes=True) == 0

    assert missing.read_text() == init.prompts.REVIEW
    assert f"Wrote {missing.relative_to(tmp_path)}." in capsys.readouterr().out


def test_init_overwrite_flag_has_the_required_help():
    parser = cli.build_parser()
    init_parser = next(
        action for action in parser._actions if action.dest == "command"
    ).choices["init"]
    overwrite = next(
        action for action in init_parser._actions if action.dest == "overwrite"
    )

    assert overwrite.help == "Replace files that already exist, discarding your edits."


def test_declining_writes_nothing(tmp_path: Path):
    # Blank answers accept every wizard default (implementer/reviewer agent,
    # then a model for each); "n" only answers the final write confirmation.
    answers = iter(["", "", "", "", "n"])
    assert init.run(
        tmp_path, assume_yes=False, confirm=lambda prompt: next(answers)
    ) != 0
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".whyline" / "relay").exists()


def test_wizard_asks_for_each_role_agent_and_model(tmp_path: Path):
    answers = iter(["claude", "codex", "opus", "", "y"])
    seen = []

    def confirm(prompt):
        seen.append(prompt)
        return next(answers)

    assert init.run(tmp_path, assume_yes=False, confirm=confirm) == 0
    assert seen == [
        "Implementer agent [codex]: ",
        "Reviewer agent [claude]: ",
        "Model for claude (blank for default): ",
        "Model for codex (blank for default): ",
        "Write these? [Y/n] ",
    ]
    text = (tmp_path / ".whyline" / "relay" / "config.toml").read_text()
    assert 'implementer = "claude"' in text and 'reviewer = "codex"' in text
    assert "[agents.claude]" in text and 'model = "opus"' in text
    assert "model" not in text.split("[agents.codex]")[1]


def test_wizard_blank_answers_keep_every_default(tmp_path: Path):
    answers = iter(["", "", "", "", "y"])
    assert init.run(
        tmp_path, assume_yes=False, confirm=lambda p: next(answers)
    ) == 0
    text = (tmp_path / ".whyline" / "relay" / "config.toml").read_text()
    assert 'implementer = "codex"' in text and 'reviewer = "claude"' in text
    assert "model" not in text


def test_wizard_rejects_an_agent_that_is_not_built_in(tmp_path: Path, capsys):
    code = init.run(tmp_path, assume_yes=False, confirm=lambda p: "gemini")
    assert code == 1
    assert "not a built-in agent" in capsys.readouterr().err.lower()
    assert not (tmp_path / ".whyline" / "relay").exists()


def test_flags_still_skip_the_agent_prompts_but_not_the_model_prompt(
    tmp_path: Path,
):
    # --implementer/--reviewer (as cli.py passes them) already answer the agent
    # questions; the wizard still offers a model for each, since a flag never
    # existed for that before this piece.
    answers = iter(["opus", "", "y"])
    seen = []

    def confirm(prompt):
        seen.append(prompt)
        return next(answers)

    code = init.run(
        tmp_path,
        assume_yes=False,
        confirm=confirm,
        implementer="claude",
        reviewer="codex",
    )
    assert code == 0
    assert seen == [
        "Model for claude (blank for default): ",
        "Model for codex (blank for default): ",
        "Write these? [Y/n] ",
    ]


def test_yes_never_prompts_even_with_no_flags(tmp_path: Path):
    def explode(prompt):
        raise AssertionError(f"assume_yes=True must never prompt: {prompt!r}")

    assert init.run(tmp_path, assume_yes=True, confirm=explode) == 0
    text = (tmp_path / ".whyline" / "relay" / "config.toml").read_text()
    assert "[roles]" not in text  # exactly today's plain-default shape


def test_says_codex_hooks_are_not_needed(tmp_path: Path, capsys):
    (tmp_path / ".whyline").mkdir()
    init.run(tmp_path, assume_yes=True)
    out = capsys.readouterr().out
    assert "codex" in out.lower()
    assert "whyline sync" in out
    assert "dangerously" not in out


def test_no_terminal_means_nothing_is_written(tmp_path: Path):
    def no_terminal(prompt):
        raise EOFError

    assert init.run(tmp_path, assume_yes=False, confirm=no_terminal) != 0
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".whyline" / "relay").exists()


def test_says_the_allowlist_is_not_a_security_boundary(tmp_path: Path, capsys):
    init.run(tmp_path, assume_yes=True)
    out = capsys.readouterr().out
    assert "not a sandbox" in out
    assert "no secrets" in out


def test_the_permissions_file_is_the_relays_own_and_leaves_claude_settings_alone(
    tmp_path: Path,
):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"model": "opus"}')
    init.run(tmp_path, assume_yes=True)
    assert json.loads((tmp_path / ".claude" / "settings.json").read_text()) == {"model": "opus"}
    command = json.loads(
        (tmp_path / ".whyline" / "relay" / "claude-settings.json").read_text()
    )
    assert "Bash(git commit:*)" in command["permissions"]["allow"]


def test_ask_model_prefills_from_whyline_model_json(tmp_path):
    from whyline_relay import init
    target = tmp_path / ".whyline" / "model.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"codex": "gpt-5-codex"}')
    answers = iter([""])
    result = init._ask_model(lambda prompt: next(answers), tmp_path, "codex")
    assert result == "gpt-5-codex"


def test_ask_model_with_a_preset_can_still_be_overridden(tmp_path):
    from whyline_relay import init
    target = tmp_path / ".whyline" / "model.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"codex": "gpt-5-codex"}')
    answers = iter(["haiku"])
    result = init._ask_model(lambda prompt: next(answers), tmp_path, "codex")
    assert result == "haiku"


def test_ask_model_with_no_preset_behaves_exactly_as_before(tmp_path):
    from whyline_relay import init
    answers = iter([""])
    result = init._ask_model(lambda prompt: next(answers), tmp_path, "codex")
    assert result is None


def test_ask_model_eof_with_a_preset_falls_back_to_it(tmp_path):
    from whyline_relay import init
    target = tmp_path / ".whyline" / "model.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"codex": "gpt-5-codex"}')
    def raise_eof(prompt):
        raise EOFError
    result = init._ask_model(raise_eof, tmp_path, "codex")
    assert result == "gpt-5-codex"


def test_ensure_relay_gitignore_creates_it_when_absent(tmp_path: Path):
    from whyline_relay import init

    assert init.ensure_relay_gitignore(tmp_path) == "created"
    content = (tmp_path / ".whyline" / "relay" / ".gitignore").read_text()
    assert content == "\n".join(init.RELAY_GITIGNORE_LINES) + "\n"
    for line in (
        "PAUSE", "skipped-tasks.json", "antigravity-declined",
        "brainstorm-plan-draft.md", "recovery.json", "agent-limits.json",
        "resolve-mode.json", "chat-active-agents.json", "cache/",
    ):
        assert line in content.splitlines()


_OLD_GITIGNORE = (
    "logs/", "state.json", "STOP", "running.json",
    "chat.json", "chat-history.jsonl", "brainstorm-tmp/",
)


def test_relay_gitignore_keeps_the_old_lines_first_and_in_order():
    assert init.RELAY_GITIGNORE_LINES[: len(_OLD_GITIGNORE)] == _OLD_GITIGNORE


def test_relay_gitignore_runtime_lines_are_all_in_relay_ignore():
    from whyline_relay import gitcheck

    for line in init.RELAY_GITIGNORE_LINES[len(_OLD_GITIGNORE):]:
        assert gitcheck.RELAY_DIR + line in gitcheck.RELAY_IGNORE


def test_ensure_relay_gitignore_appends_missing_lines_once(tmp_path: Path):
    target = tmp_path / ".whyline" / "relay" / ".gitignore"
    target.parent.mkdir(parents=True)
    target.write_text("notes.md\n" + "\n".join(_OLD_GITIGNORE) + "\n")

    assert init.ensure_relay_gitignore(tmp_path) == "updated"
    lines = target.read_text().splitlines()
    assert lines[0] == "notes.md"
    assert lines[1:8] == list(_OLD_GITIGNORE)
    assert lines[8] == init.GITIGNORE_ADDED_COMMENT
    assert lines[9:] == list(init.RELAY_GITIGNORE_LINES[len(_OLD_GITIGNORE):])

    before = target.read_bytes()
    assert init.ensure_relay_gitignore(tmp_path) is None
    assert target.read_bytes() == before


def test_ensure_relay_gitignore_without_create_leaves_a_missing_file(tmp_path: Path):
    assert init.ensure_relay_gitignore(tmp_path, create=False) is None
    assert not (tmp_path / ".whyline" / "relay" / ".gitignore").exists()


def test_init_reports_an_updated_gitignore(tmp_path: Path, capsys):
    target = tmp_path / ".whyline" / "relay" / ".gitignore"
    target.parent.mkdir(parents=True)
    target.write_text("\n".join(_OLD_GITIGNORE) + "\n")

    assert init.run(tmp_path, assume_yes=True) == 0
    out = capsys.readouterr().out
    assert "Updated .whyline/relay/.gitignore." in out
    assert "Wrote .whyline/relay/.gitignore." not in out

    init.run(tmp_path, assume_yes=True)
    assert "Updated .whyline/relay/.gitignore." not in capsys.readouterr().out


def test_relay_gitignore_covers_brainstorm_temp_files(tmp_path: Path):
    from whyline_relay import init

    init.ensure_relay_gitignore(tmp_path)
    content = (tmp_path / ".whyline" / "relay" / ".gitignore").read_text()
    assert "brainstorm-tmp/" in content


_LEGACY_CONFIG = (
    '[roles]\nimplementer = "codex"\nreviewer = "claude"\n'
    '[agents.codex]\ncommand = ["codex"]\n'
    '[agents.claude]\ncommand = ["claude"]\n'
)
_PAUSE_HINT = (
    "`.whyline/relay/PAUSE` is in git; run `git rm --cached .whyline/relay/PAUSE` "
    "and commit, so other checkouts don't pause."
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _relay_repo(two_role_repo, extra: dict[str, str]) -> Path:
    root = two_role_repo("- [ ] T-1: build\n")
    (root / ".whyline/relay/config.toml").write_text(_LEGACY_CONFIG)
    for relative, content in extra.items():
        (root / relative).write_text(content)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "config")
    return root


def _fake_agent(monkeypatch, turns: list[dict], seen: list[str]) -> None:
    """Stand in for agents.run. Before each turn, records the relay's
    .gitignore as the agent would find it."""
    from whyline_relay import loop

    def run(command, prompt, *, cwd, log_path, **kwargs):
        turn = turns.pop(0)
        root = Path(cwd)
        gitignore = root / ".whyline/relay/.gitignore"
        seen.append(gitignore.read_text() if gitignore.exists() else "")
        if turn.get("pause"):
            loop.request_pause(root)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok\n")
        task_id = re.search(r"^## Task (\S+)", prompt, re.M).group(1)
        if turn.get("commit"):
            (root / f"work-{task_id}.txt").write_text("x\n")
            # "only": commit the work alone, as a reviewer staging by name would.
            _git(root, "add", *(
                [f"work-{task_id}.txt"] if turn["commit"] == "only" else ["-A"]
            ))
            _git(root, "commit", "-m", f"feat: work ({task_id})")
        target = root / ".whyline" / "active-handoff.json"
        previous = json.loads(target.read_text()) if target.exists() else {}
        counter = int(previous.get("counter", 0)) + 1
        target.write_text(json.dumps({
            "id": f"event{counter}", "counter": counter, "task": task_id,
            "to_actor": turn["to"], "status": turn["status"],
            "summary": f"{turn['status']} summary", "from_actor": "",
        }))
        return 0

    monkeypatch.setattr(loop.agents, "run", run)


def test_start_with_a_tracked_pause_prints_the_untrack_hint_once(
    two_role_repo, monkeypatch, capsys
):
    root = _relay_repo(two_role_repo, {".whyline/relay/PAUSE": "{}"})
    seen: list[str] = []
    _fake_agent(monkeypatch, [{"to": "claude", "status": "ready-for-review"}], seen)

    cli.main(["start", "--repo", str(root)])

    out = capsys.readouterr().out
    assert out.count(_PAUSE_HINT) == 1
    assert "STOP` is in git" not in out


def test_start_without_a_tracked_marker_prints_no_hint(
    two_role_repo, monkeypatch, capsys
):
    root = _relay_repo(two_role_repo, {})
    seen: list[str] = []
    _fake_agent(monkeypatch, [
        {"to": "claude", "status": "ready-for-review"},
        {"to": "claude", "status": "approved", "commit": True},
    ], seen)

    assert cli.main(["start", "--repo", str(root)]) == cli.EXIT_OK
    assert "is in git; run `git rm --cached" not in capsys.readouterr().out
    assert not (root / ".whyline/relay/.gitignore").exists()


def _real_tree_check(monkeypatch) -> None:
    """Put back preflight's own clean-tree check, which conftest stubs out.

    Only the working-tree result is kept: the rest needs real agents and a
    real whyline."""
    from whyline_relay import preflight

    def launch_checks(root, plan_path, *, allow_dirty):
        checks = preflight.run(
            root, plan_path, allow_dirty=allow_dirty,
            runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
        )
        return [check for check in checks if "working tree" in check.message]

    monkeypatch.setattr(cli, "_launch_checks", launch_checks)


def _status(root: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True
    ).stdout


def _assert_updated(text: str) -> None:
    lines = text.splitlines()
    assert lines[: len(_OLD_GITIGNORE)] == list(_OLD_GITIGNORE)
    assert init.GITIGNORE_ADDED_COMMENT in lines
    for line in init.RELAY_GITIGNORE_LINES:
        assert lines.count(line) == 1


def test_start_commits_an_old_gitignore_so_a_pause_then_resume_works(
    two_role_repo, monkeypatch, capsys
):
    old = "\n".join(_OLD_GITIGNORE) + "\n"
    root = _relay_repo(two_role_repo, {".whyline/relay/.gitignore": old})
    _real_tree_check(monkeypatch)
    seen: list[str] = []
    _fake_agent(monkeypatch, [
        {"to": "claude", "status": "ready-for-review", "pause": True},
        {"to": "claude", "status": "approved", "commit": True},
    ], seen)

    assert cli.main(["start", "--repo", str(root)]) == cli.EXIT_PAUSED
    capsys.readouterr()
    _assert_updated(seen[0])
    # The update is its own commit, so the pause leaves nothing behind.
    assert _status(root) == ""
    log = subprocess.run(
        ["git", "log", "--format=%s", "-1", "--", ".whyline/relay/.gitignore"],
        cwd=root, capture_output=True, text=True,
    ).stdout
    assert log.strip() == "chore: update .whyline/relay/.gitignore"

    assert cli.main(["resume", "--repo", str(root)]) == cli.EXIT_OK, (
        capsys.readouterr().err
    )
    assert _status(root) == ""


def test_resume_updates_an_old_gitignore_before_the_first_turn(
    two_role_repo, monkeypatch, capsys
):
    from whyline_relay import loop

    old = "\n".join(_OLD_GITIGNORE) + "\n"
    root = _relay_repo(two_role_repo, {".whyline/relay/.gitignore": old})
    _real_tree_check(monkeypatch)
    seen: list[str] = []
    _fake_agent(monkeypatch, [
        {"to": "claude", "status": "ready-for-review", "pause": True},
        {"to": "claude", "status": "changes-requested", "pause": True},
        {"to": "claude", "status": "ready-for-review"},
        {"to": "claude", "status": "approved", "commit": "only"},
    ], seen)
    # A task paused by a release that predates the update.
    fix = loop._fix_relay_gitignore
    monkeypatch.setattr(loop, "_fix_relay_gitignore", lambda root, *, commit: None)
    assert cli.main(["start", "--repo", str(root)]) == cli.EXIT_PAUSED
    monkeypatch.setattr(loop, "_fix_relay_gitignore", fix)
    capsys.readouterr()
    assert (root / ".whyline/relay/.gitignore").read_text() == old

    # Mid-task, so the update waits in the tree rather than moving the base;
    # pausing again on it must not make the next resume refuse a dirty tree.
    assert cli.main(["resume", "--repo", str(root)]) == cli.EXIT_PAUSED
    err = capsys.readouterr().err
    _assert_updated(seen[1])
    assert "uncommitted" not in err
    assert _status(root) == " M .whyline/relay/.gitignore\n"

    assert cli.main(["resume", "--repo", str(root)]) == cli.EXIT_OK, (
        capsys.readouterr().err
    )
    # The reviewer committed only its work; the tick commit carried the update.
    assert _status(root) == ""
    committed = subprocess.run(
        ["git", "show", "HEAD:.whyline/relay/.gitignore"],
        cwd=root, capture_output=True, text=True,
    ).stdout
    _assert_updated(committed)
