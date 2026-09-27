"""The `whyline-relay chat` REPL: setup, routing, and the turn pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from whyline_relay import adapters, agents, chatlog, config, failover, gitcheck, init, invocation

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


def _ensure_permission_files(root: Path, agent: str) -> bool:
    """Generate a managed agent's permission file(s) (e.g. claude's
    claude-settings.json) if this repo's own `init` command was never run.
    Returns True if anything was newly created.

    claude's own managed default_command references that file directly --
    without it, the real claude CLI fails outright with "Settings file not
    found," which the init flow normally prevents by writing it up front.
    chat must not assume init ever ran (spec D5: claude/codex work in chat
    with zero relay config present), so it generates the same file here,
    once, the same way init.run does -- and never overwrites an existing
    one, so a user's own customized settings are left alone.
    """
    if agent not in adapters.BUILTIN:
        return False
    stack = init.detect_stack(root)
    relay = config.relay_dir(root)
    created = False
    for key, text in adapters.BUILTIN[agent].permission_files(stack).items():
        path = relay / key
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            created = True
    return created


def _execute_agent_call(
    root: Path, agent: str, prompt: str, full_prompt: str,
    settings: "config.Config", run_fn,
) -> dict:
    """Runs one attempt against `agent`. No chatlog write, no failover
    logic -- run_turn decides, after seeing the result, whether this was
    the whole story or whether a backup needs a turn too."""
    command = resolve_command(settings, agent)
    adapter = config.adapter_for(settings, agent)
    if _ensure_permission_files(root, agent):
        # Committed on its own, before the turn -- so the turn's own
        # diff-stat/files_changed reflects only what the agent did, not
        # one-time setup init would normally have already done.
        gitcheck.commit_all(root, f"chat: generate {agent}'s permission settings")
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
    # commit_all stages everything and no-ops (returns False) when the tree
    # is already clean -- safe to call unconditionally rather than checking
    # is_dirty first, and it's the only reliable way to see a brand-new
    # untracked file in the resulting stat (git diff on the working tree
    # never shows untracked files; the committed diff always does).
    committed = gitcheck.commit_all(root, f"chat: {agent} turn")
    diff_stat = gitcheck.commit_stat(root) if committed else ""
    files_changed = max(len(diff_stat.splitlines()) - 1, 0) if diff_stat else 0
    return {
        "raw": raw,
        "response": response,
        "ok": ok,
        "adapter": adapter,
        "command": turn_command,
        "committed": committed,
        "diff_stat": diff_stat,
        "files_changed": files_changed,
    }


def run_turn(
    root: Path,
    *,
    agent: str,
    prompt: str,
    settings: "config.Config | None" = None,
    run_fn=None,
    runner=subprocess.run,
) -> dict:
    settings = settings if settings is not None else config.load(root)
    run_fn = run_fn if run_fn is not None else agents.run
    requested = agent
    resolved = failover.resolve_chat_agent(root, requested)
    full_prompt = _build_prompt(root, prompt)
    attempt = _execute_agent_call(root, resolved, prompt, full_prompt, settings, run_fn)
    final_agent = resolved
    failover_notice: str | None = None
    if resolved == requested:
        # Check `chat_backup` *before* calling failover_reason: that call can
        # run a real login-status subprocess (via runner), and there is no
        # point spending it -- or risking a real, unmocked subprocess call in
        # a test that never configured [chat.backup] -- when there is no
        # backup to switch to anyway. This also means every pre-existing
        # chat test, none of which configure [chat.backup], never reaches
        # failover_reason at all: zero behavior change for them.
        backup = settings.chat_backup.get(requested)
        if backup:
            reason = failover.failover_reason(
                attempt["adapter"], attempt["raw"], attempt["command"], runner=runner
            )
            if reason:
                verb, _ = failover.REASON_TEXT[reason]
                failover_notice = f"{requested} {verb}; trying its backup, {backup}..."
                failover.write_override(
                    root,
                    requested,
                    failover.ActiveOverride(
                        agent=backup,
                        backup_for=requested,
                        reason=reason,
                        since=datetime.now(timezone.utc).isoformat(),
                    ),
                    storage_path=failover.chat_path(root),
                )
                attempt = _execute_agent_call(
                    root, backup, prompt, full_prompt, settings, run_fn
                )
                final_agent = backup
                reason2 = failover.failover_reason(
                    attempt["adapter"], attempt["raw"], attempt["command"],
                    runner=runner,
                )
                if reason2:
                    override = failover.read_overrides(
                        root, failover.chat_path(root)
                    ).get(requested)
                    failover_notice = failover.pause_message(
                        backup, requested, reason2, override
                    )
    else:
        # Already on an active backup (someone configured [chat.backup] for
        # `requested` at some point, and it already switched) -- detect a
        # further failure but never chase a third agent (one hop only).
        reason = failover.failover_reason(
            attempt["adapter"], attempt["raw"], attempt["command"], runner=runner
        )
        if reason:
            override = failover.read_overrides(root, failover.chat_path(root)).get(
                requested
            )
            failover_notice = failover.pause_message(
                resolved, requested, reason, override
            )
    rate_limited = agents.rate_limited(attempt["raw"])
    # append generates the timestamp and returns the record it persisted.
    # rate_limited, diff_stat, and failover_notice are return-only, so they
    # are added after the write and never become part of the chatlog line.
    record = chatlog.append(
        root, agent=final_agent, prompt=prompt, response=attempt["response"],
        files_changed=attempt["files_changed"], ok=attempt["ok"],
    )
    record["rate_limited"] = rate_limited
    if attempt["committed"]:
        record["diff_stat"] = attempt["diff_stat"]
    if failover_notice:
        record["failover_notice"] = failover_notice
    return record


SLASH_COMMANDS = ("/default", "/agents", "/history", "/clear", "/exit")


def _agent_status_lines(settings: "config.Config", which) -> list[str]:
    lines = []
    for name in CHAT_AGENTS:
        configured = name in settings.agents
        found = which(name) is not None
        if configured and found:
            state = "installed, configured"
        elif found:
            state = "installed, not configured for chat"
        elif configured:
            state = "configured, but not installed"
        else:
            state = "not available"
        lines.append(f"{name}: {state}")
    return lines


def repl(
    root: Path,
    *,
    input_fn=None,
    print_fn=None,
    run_fn=None,
    which=None,
    setup_answers=None,
) -> None:
    input_fn = input_fn if input_fn is not None else input
    print_fn = print_fn if print_fn is not None else print
    which = which if which is not None else shutil.which

    default_agent = load_default_agent(root)
    if default_agent is None:
        wizard_input = input_fn
        if setup_answers is not None:
            def wizard_input(_prompt=""):
                return next(setup_answers)

        default_agent = run_setup_wizard(
            root, which=which, input_fn=wizard_input, print_fn=print_fn
        )

    settings = config.load(root)
    while True:
        line = input_fn("> ").strip()
        if not line:
            continue
        if line == "/exit":
            return
        if line.startswith("/") and line.split()[0] not in (
            *(f"/{a}" for a in CHAT_AGENTS),
            *SLASH_COMMANDS,
        ):
            print_fn(
                f"Unknown command: {line.split()[0]}. Try "
                + ", ".join(f"/{a}" for a in CHAT_AGENTS)
                + ", " + ", ".join(SLASH_COMMANDS) + "."
            )
            continue
        if line == "/agents":
            for status_line in _agent_status_lines(settings, which):
                print_fn(status_line)
            continue
        if line == "/history":
            for turn in chatlog.load(root):
                print_fn(f"[{turn['agent']}] {turn['prompt']}")
                print_fn(turn["response"])
            continue
        if line == "/clear":
            confirm = input_fn(
                "Clear all chat history for this repo? [y/N] "
            ).strip().lower()
            if confirm == "y":
                chatlog.clear(root)
                print_fn("History cleared.")
            continue
        if line.startswith("/default"):
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[1].strip() in CHAT_AGENTS:
                save_default_agent(root, parts[1].strip())
                default_agent = parts[1].strip()
                print_fn(f"Default agent is now {default_agent}.")
            else:
                print_fn(f"Usage: /default <{'|'.join(CHAT_AGENTS)}>")
            continue

        agent = default_agent
        prompt = line
        first_word = line.split(maxsplit=1)[0]
        if first_word in (f"/{a}" for a in CHAT_AGENTS):
            agent = first_word[1:]
            rest = line.split(maxsplit=1)
            prompt = rest[1] if len(rest) == 2 else ""

        try:
            record = run_turn(
                root, agent=agent, prompt=prompt, settings=settings, run_fn=run_fn
            )
        except AgentUnavailable as error:
            print_fn(str(error))
            continue
        except agents.AgentMissing as error:
            print_fn(str(error))
            continue
        except agents.AgentTimeout as error:
            print_fn(f"{error} -- try again, or /default another agent.")
            continue

        print_fn(f"[{record['agent']}] {record['response']}")
        if record["rate_limited"]:
            print_fn(
                f"{record['agent']} looks rate-limited -- "
                "/default another agent, or wait."
            )
        if record.get("diff_stat"):
            prefix = "" if record["ok"] else "⚠ "
            print_fn(f"{prefix}{record['diff_stat'].strip()}")
