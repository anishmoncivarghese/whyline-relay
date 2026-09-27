"""The `whyline-relay chat` REPL: setup, routing, and the turn pipeline."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from whyline_relay import agents, chatlog, config, gitcheck, init, invocation

CHAT_AGENTS = ("claude", "codex", "agy", "grok")


class NoAgentsInstalled(RuntimeError):
    """None of claude/codex/agy/grok are on PATH."""


def chat_config_path(root: Path) -> Path:
    return root / ".whyline" / "relay" / "chat.json"


def load_default_agent(root: Path) -> str | None:
    path = chat_config_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    agent = data.get("default_agent")
    return agent if isinstance(agent, str) else None


def save_default_agent(root: Path, agent: str) -> None:
    path = chat_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"default_agent": agent}) + "\n")


def run_setup_wizard(
    root: Path,
    *,
    which=None,
    input_fn=None,
    print_fn=None,
) -> str:
    which = which if which is not None else shutil.which
    input_fn = input_fn if input_fn is not None else input
    print_fn = print_fn if print_fn is not None else print

    installed = [name for name in CHAT_AGENTS if which(name) is not None]
    if not installed:
        raise NoAgentsInstalled(
            "none of claude, codex, agy, grok were found on PATH"
        )
    missing = [name for name in CHAT_AGENTS if name not in installed]
    print_fn(f"Detected: {', '.join(installed)}.")
    if missing:
        print_fn(f"Not found: {', '.join(missing)}.")

    default_hint = installed[0]
    while True:
        answer = input_fn(f"Pick your default agent [{default_hint}]: ").strip()
        chosen = answer or default_hint
        if chosen in installed:
            break
        print_fn(f"{chosen} is not installed here -- pick one of: {', '.join(installed)}")

    save_default_agent(root, chosen)
    init.ensure_relay_gitignore(root)
    print_fn(f"Saved. Starting chat -- default agent is {chosen}.")
    return chosen


CHAT_TIMEOUT_SECONDS = 300


class AgentUnavailable(RuntimeError):
    """This agent has no command configured for chat in this repo."""


def resolve_command(settings: "config.Config", agent: str) -> list[str]:
    command = settings.agents.get(agent)
    if command is None:
        raise AgentUnavailable(
            f"{agent} is not configured for chat in this repo -- see "
            f"`{invocation.command('doctor')}` and the README, "
            f"'Using {agent} today', to add it to .whyline/relay/config.toml."
        )
    return list(command)


def _build_prompt(root: Path, new_input: str) -> str:
    history = chatlog.recent(root)
    return f"{history}\n\n{new_input}" if history else new_input


def run_turn(
    root: Path,
    *,
    agent: str,
    prompt: str,
    settings: "config.Config | None" = None,
    run_fn=None,
) -> dict:
    settings = settings if settings is not None else config.load(root)
    run_fn = run_fn if run_fn is not None else agents.run
    command = resolve_command(settings, agent)
    adapter = config.adapter_for(settings, agent)
    full_prompt = _build_prompt(root, prompt)
    turn_command = list(command)
    output_file: Path | None = None
    if adapter.uses_output_file:
        handle = tempfile.NamedTemporaryFile(
            prefix="whyline-relay-chat-", suffix=".txt", delete=False
        )
        output_file = Path(handle.name)
        handle.close()
        turn_command += ["-o", str(output_file)]
    log_path = config.relay_dir(root) / "logs" / "chat-last-turn.log"
    result = run_fn(
        turn_command,
        full_prompt,
        cwd=root,
        log_path=log_path,
        timeout_seconds=CHAT_TIMEOUT_SECONDS,
        capture=True,
        echo=True,
        agent_name=agent,
    )
    if adapter.uses_output_file:
        raw = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
    else:
        raw = result.output or ""
    response = adapter.extract_response(raw)
    ok = result.exit_code == 0
    rate_limited = agents.rate_limited(raw)
    # commit_all stages everything and no-ops (returns False) when the tree
    # is already clean -- safe to call unconditionally rather than checking
    # is_dirty first, and it's the only reliable way to see a brand-new
    # untracked file in the resulting stat (git diff on the working tree
    # never shows untracked files; the committed diff always does).
    committed = gitcheck.commit_all(root, f"chat: {agent} turn")
    diff_stat = gitcheck.commit_stat(root) if committed else ""
    files_changed = max(len(diff_stat.splitlines()) - 1, 0) if diff_stat else 0
    # append generates the timestamp and returns the record it persisted.
    # rate_limited and diff_stat are return-only, so they are added after
    # the write and never become part of the chatlog line.
    record = chatlog.append(
        root, agent=agent, prompt=prompt, response=response,
        files_changed=files_changed, ok=ok,
    )
    record["rate_limited"] = rate_limited
    if committed:
        record["diff_stat"] = diff_stat
    return record
