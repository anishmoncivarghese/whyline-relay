"""The state machine. The only module in the relay that decides anything."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from whyline_relay import (
    adapters,
    agents,
    attachments as attachment_delivery,
    config,
    failover,
    gitcheck,
    handoff,
    invocation,
    pipeline,
    plan,
    prompts,
    routing,
    running,
    state,
    testplan,
    whylinecmd,
)
from whyline_relay.adapters import bypass, grok

RELEASE_PREFIX = "release task for you: "
GROK_RESUMES = grok.RESUMES


class Paused(RuntimeError):
    """The relay stopped and wants a human. Carries the reason and the log path."""

    def __init__(self, reason: str, log_path: Path | None = None):
        super().__init__(reason)
        self.reason = reason
        self.log_path = log_path


def task_repo(root: Path, task: plan.Task) -> Path:
    """The repository a task's code lives in: its `relay-repo:` line, else the
    relay's own. Only the task's git work moves there; the plan, state,
    handoffs and logs stay in `root`, so one relay can drive work in another
    checkout without the user switching repositories mid-plan."""
    if not task.repo:
        return root.resolve()
    repo = Path(task.repo).expanduser()
    if not repo.is_absolute():
        repo = root / repo
    repo = repo.resolve()
    try:
        top = Path(gitcheck._git(repo, "rev-parse", "--show-toplevel")).resolve()
    except (gitcheck.GitError, OSError):
        top = None
    if top != repo:
        raise Paused(
            f"{task.task_id}: relay-repo {task.repo} is not the top of a git "
            "repository; fix that line in the plan, then resume"
        )
    return repo


@dataclass(frozen=True)
class Outcome:
    task_id: str
    rounds: int
    committed: bool


# A back-route whose summary contains one of these, case-insensitively, is a
# refused command rather than a defect. Substring match is intentional.
SANDBOX_MARKERS = (
    "permission denied",
    "operation not permitted",
    "sandbox",
    "was denied",
    "not permitted",
)


def _summary_reports_sandbox_refusal(summary: str) -> bool:
    folded = summary.casefold()
    return any(marker in folded for marker in SANDBOX_MARKERS)


def _sandbox_backroute(
    pipe: pipeline.Pipeline,
    profile_name: str,
    current_stage_id: str,
    target_stage_id: str | None,
    summary: str,
) -> bool:
    """True when this advance would send a refused command back to an earlier stage."""
    if not target_stage_id or not _summary_reports_sandbox_refusal(summary):
        return False
    order = pipe.profiles[profile_name].stages
    try:
        return order.index(target_stage_id) < order.index(current_stage_id)
    except ValueError:
        return False


def _sandbox_pause_reason(agent: str, summary: str) -> str:
    return f"{agent} could not run a required command: {summary}"


def _blocked_reason(agent: str, record: handoff.Handoff) -> str:
    reason = f"{agent} reported blocked: {record.summary or 'no summary given'}"
    for question in record.questions:
        reason += f". Question: {question}"
    if len(reason) > 300:
        return reason[:297] + "..."
    return reason


def check_visit_cap(
    pipe: pipeline.Pipeline,
    stage_id: str,
    stage_visits: dict[str, int],
    task: plan.Task,
) -> None:
    cap = pipe.stages[stage_id].max_visits
    if stage_visits[stage_id] > cap:
        raise Paused(
            f"{task.task_id} hit stage {stage_id!r}'s {cap}-visit cap without "
            'reaching "@complete"',
            None,
        )


def log_path(
    root: Path, task_id: str, round_: int, agent: str, role: str = ""
) -> Path:
    suffix = f"-{role}" if role else ""
    return root / ".whyline" / "relay" / "logs" / (
        f"{task_id}-{round_}-{agent}{suffix}.log"
    )


def _command_with_task_dir(
    command: list[str],
    flag: tuple[str, ...] | None,
    root: Path,
    repo: Path,
) -> list[str]:
    """Append `flag` and the task repository when that repository is not `root`.

    `agents.build_argv` puts the prompt last, so the flag stays in the command.
    An exact flag-plus-path already present is left alone. The same flag aimed
    at a different directory does not count: that directory is not this task.
    """
    if not flag or repo == root.resolve():
        return command
    path = str(repo)
    needle = (*flag, path)
    width = len(needle)
    if any(
        tuple(command[index : index + width]) == needle
        for index in range(len(command) - width + 1)
    ):
        return command
    return [*command, *needle]


def _uv_cache_env(root: Path) -> dict[str, str] | None:
    """Point the child's uv at the relay cache, unless one is already configured.

    The path sits under `root`, so `uv run` in the relay checkout and in a task
    repository both write inside this workspace. No other variable is set. An
    existing `UV_CACHE_DIR` wins by leaving the child on the inherited environment.
    """
    if "UV_CACHE_DIR" in os.environ:
        return None
    return {"UV_CACHE_DIR": str(root / ".whyline" / "relay" / "cache" / "uv")}


def run_agent(
    root: Path,
    settings: config.Config,
    agent: str,
    role: str,
    template_name: str,
    task: plan.Task,
    round_: int,
    review_feedback: str,
    echo: bool,
    *,
    implementer: str,
    reviewer: str,
    action_label: str | None = None,
    log_suffix: str | None = None,
    actor: str = "",
    stage: str = "",
    profile: str = "",
    prompt_suffix: str = "",
    attachments: Sequence[Path] = (),
    runner: failover.Runner = subprocess.run,
) -> Path:
    """Render the prompt, run the agent, and return the log path."""
    _refuse_low_disk(root)
    adapter = config.adapter_for(settings, agent)
    repo = task_repo(root, task)
    command = _command_with_task_dir(
        list(settings.agents[agent]), adapter.dir_flag, root, repo
    )
    command = attachment_delivery.command_with_images(
        command, adapter.name, attachments
    )
    # The directory flag is on the command that will actually run. It is not a
    # permission-bypass flag; a bypass flag already in the command still is.
    found = bypass.find(adapter.name, command)
    if found:
        raise Paused(
            f"refusing to run {agent}: its command contains a permission-bypass "
            f"flag ({', '.join(found)}). The relay never runs an agent that way; "
            "remove it from .whyline/relay/config.toml",
            None,
        )
    running.start_turn(root, agent, task.task_id, round_, role=role)
    packet = whylinecmd.sync(root, task.task_id)
    prompt = prompts.render(
        prompts.load(root, template_name),
        task_id=task.task_id,
        task_text=task.text,
        sync_packet=packet,
        round_=round_,
        review_feedback=review_feedback,
        implementer=implementer,
        reviewer=reviewer,
        actor=actor,
        role=role,
        stage=stage,
        profile=profile,
    ) + prompt_suffix + prompts.repository_footer(root, repo)
    block = attachment_delivery.prompt_block(attachments, root)
    if block:
        prompt = f"{prompt}\n\n{block}"
    log_role = (
        log_suffix
        if log_suffix is not None
        else (role if implementer == reviewer else "")
    )
    target = log_path(root, task.task_id, round_, agent, log_role)
    action = action_label or (
        "implementing" if role == "implementer" else "reviewing"
    )
    if echo:
        agents.print_status(
            f"==> {agent}: {action} {task.task_id} "
            f"(round {round_} of {settings.max_rounds})"
        )
    # agents.run rejects 0. A configured 0 is "no watchdog", so pass None.
    timeout_seconds = (
        None if settings.timeout_minutes == 0 else settings.timeout_minutes * 60
    )
    # The main thread blocks inside the child. A side thread sets this when
    # STOP appears; agents.run kills the child and raises AgentCancelled.
    cancel_event = threading.Event()
    stop_watching = threading.Event()
    watcher = threading.Thread(
        target=_watch_for_stop,
        args=(root, cancel_event, stop_watching),
        name="whyline-stop",
        daemon=True,
    )
    started = time.monotonic()
    turn_command, turn_prompt = command, prompt
    resumes = 0
    watcher.start()
    try:
        try:
            while True:
                # A STOP file that landed after the previous turn returned must
                # not start another child. agents.run raises before spawning
                # when the event is already set. The watcher covers the file
                # appearing while this call is in flight.
                if stop_requested(root):
                    cancel_event.set()
                agents.run(
                    turn_command,
                    turn_prompt,
                    cwd=root,
                    log_path=target,
                    timeout_seconds=timeout_seconds,
                    echo=echo,
                    agent_name=agent,
                    cancel_event=cancel_event,
                    env=_uv_cache_env(root),
                )
                session = None
                if resumes < GROK_RESUMES:
                    try:
                        text = target.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        text = ""
                    session = grok.resumable_session(adapter, text, command)
                if session is None:
                    break
                # The log of the last attempt stays at `target`, where the
                # no-handoff diagnosis reads it; earlier ones are kept beside it.
                resumes += 1
                target.replace(
                    target.with_name(f"{target.stem}-cancelled-{resumes}.log")
                )
                if echo:
                    agents.print_status(
                        f"==> relay: {agent}'s turn was cancelled by its "
                        f"permission policy; resuming its session "
                        f"({resumes} of {GROK_RESUMES})"
                    )
                if cancel_event.is_set():
                    name = command[0] if command else "agent"
                    raise agents.AgentCancelled(f"{name} cancelled by user")
                turn_command = grok.resume_command(command, session)
                turn_prompt = grok.resume_prompt(command)
        finally:
            if echo:
                agents.print_status(
                    f"<== {agent} finished in "
                    f"{agents.format_duration(time.monotonic() - started)}"
                )
    except (agents.AgentTimeout, agents.AgentCancelled) as error:
        raise Paused(str(error), target) from error
    except agents.AgentMissing as error:
        raise Paused(str(error), target) from error
    finally:
        stop_watching.set()
        watcher.join()
    return target


def _no_handoff_detail(
    target: Path, adapter: adapters.Adapter, command: list[str]
) -> str:
    """Why an agent that handed nothing off probably stopped, for the human only.

    Never used to route. The agent adapter interprets its own output format.
    """
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    structured = grok.cancellation_detail(adapter, text, command)
    if structured:
        return structured
    return adapter.diagnose(text)


_CONFIG_ONLY = ".whyline/relay/config.toml"


def _short_sha(commit: str) -> str:
    return commit[:7]


def _settings_commits(repo: Path, base: str) -> list[tuple[str, str]] | None:
    """Commits in `base..HEAD` when every one touches only the relay config.

    None when any of those commits touches another path, or the range cannot
    be read. An empty list means HEAD is still `base`.
    """
    head = gitcheck.head_commit(repo)
    if head == base:
        return []
    try:
        listing = gitcheck._git(repo, "rev-list", "--reverse", f"{base}..{head}")
    except gitcheck.GitError:
        return None
    found: list[tuple[str, str]] = []
    for commit in listing.splitlines():
        if not commit:
            continue
        try:
            names = gitcheck._git(
                repo,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                commit,
            )
        except gitcheck.GitError:
            return None
        files = [line.strip() for line in names.splitlines() if line.strip()]
        if files != [_CONFIG_ONLY]:
            return None
        message = gitcheck.commit_message(repo, commit).splitlines()
        subject = message[0].strip() if message else ""
        found.append((commit, subject))
    return found


def _advance_base_past_settings(
    repo: Path, task: plan.Task, base_commit: str, *, echo: bool
) -> str | None:
    """Return HEAD when `base_commit..HEAD` is config.toml only.

    None means some commit touches another path, and the caller keeps today's
    pause. The progress line names each settings commit and the new base.
    """
    described = _settings_commits(repo, base_commit)
    if described is None:
        return None
    if not described:
        return base_commit
    head = gitcheck.head_commit(repo)
    if echo:
        short_head = _short_sha(head)
        for commit, subject in described:
            agents.print_status(
                f"Settings changed during {task.task_id} "
                f"({_short_sha(commit)} {subject}); continuing from {short_head}."
            )
    return head


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _refuse_low_disk(root: Path) -> None:
    """Pause before a turn when the volume has under 1 GB free."""
    low = agents.low_disk_message(shutil.disk_usage(root).free)
    if low is None:
        return
    print(f"  FAIL  {low}")
    raise Paused(low)


def _mount_point(path: Path) -> str:
    try:
        current = path.resolve()
    except OSError:
        current = path
    if not current.is_dir():
        current = current.parent
    last = current
    while True:
        if os.path.ismount(current):
            return str(current)
        parent = current.parent
        if parent == current:
            return str(last)
        current = parent


def _die_disk_full(error: state.DiskFull) -> None:
    """Tell the operator and stop. No traceback: the disk, not a bug, failed."""
    mb = max(0, error.free_bytes) // (1024 * 1024)
    volume = _mount_point(error.path)
    print(
        f"Disk full: {mb} MB free on {volume}. "
        "Free some space, then run whyline relay resume."
    )
    raise SystemExit(3)


def _failover_no_handoff(
    root: Path,
    settings: config.Config,
    *,
    agent: str,
    role: str,
    text: str,
    target: Path,
    runner: failover.Runner,
    echo: bool,
    ephemeral: dict[str, str],
    ephemeral_tried: dict[str, list[str]],
) -> bool:
    """Handle a turn that produced no handoff.

    True means retry this stage on another agent. False means the text is not
    a failover reason. A Paused stop is raised when nothing usable remains.
    Overload retries for this turn only and writes no sticky override.
    """
    adapter = config.adapter_for(settings, agent)
    command = settings.agents.get(agent, [])
    reason = failover.classify(adapter, text, command, runner=runner)
    if reason is None:
        return False
    if reason == "overload":
        tried = list(ephemeral_tried.get(role, []))
        if agent not in tried:
            tried.append(agent)
        backup = failover.next_backup(settings.backup_chain, set(tried))
        if backup is None:
            last = _last_nonempty_line(text)
            raise Paused(
                f"{agent} hit a temporary capacity error ({last}); "
                "resume in a few minutes or change its model",
                target,
            )
        ephemeral[role] = backup
        ephemeral_tried[role] = tried
        if echo:
            agents.print_status(
                f"{agent}: model at capacity; using {backup} for this turn"
            )
        return True

    existing = failover.read_overrides(root).get(role)
    tried_sticky = list(existing.tried) if existing is not None else []
    if agent not in tried_sticky:
        tried_sticky.append(agent)
    backup = failover.next_backup(settings.backup_chain, set(tried_sticky))
    if backup is not None:
        verb, _ = failover.REASON_TEXT[reason]
        visible = grok.rate_limit_text(adapter, text, command)
        failover.write_override(
            root,
            role,
            failover.ActiveOverride(
                agent=backup,
                backup_for=agent,
                reason=reason,
                since=datetime.now().astimezone().isoformat(),
                tried=tried_sticky,
                resets_at=failover.reset_deadline(visible) if reason == "rate-limit" else "",
            ),
        )
        if echo:
            agents.print_status(
                f"==> relay: {role} switched from {agent} to {backup} "
                f"({agent} {verb})"
            )
        return True
    if not settings.backup_chain:
        raise Paused(failover.pause_message(agent, role, reason, existing), target)
    raise Paused(
        f"every backup in the chain is unavailable for {role} "
        f"({', '.join(sorted(tried_sticky))} all failed)",
        target,
    )


def _approved(
    root: Path, task: plan.Task, base_commit: str, round_: int, log: Path | None
) -> Outcome:
    """An approval counts only when a real commit naming the task exists."""
    if not gitcheck.commit_verified(task_repo(root, task), base_commit, task.task_id):
        raise Paused(
            f"{task.task_id} was approved but no commit naming it exists; "
            "the checkbox was not ticked",
            log,
        )
    return Outcome(task_id=task.task_id, rounds=round_, committed=True)


_TYPES = ("feat", "fix", "docs", "test", "chore", "refactor")
_TYPE_PREFIX = re.compile(r"^(feat|fix|docs|test|chore|refactor)(\([^)]*\))?:\s*", re.I)


def commit_message(task: plan.Task, summary: str) -> str:
    summary = (summary or "").strip()
    if not summary:
        return f"chore: {task.task_id} needed no changes ({task.task_id})"
    match = _TYPE_PREFIX.match(summary)
    if match:
        kind, summary = match.group(1).lower(), summary[match.end():]
    else:
        title = task.text.split(":", 1)[-1].strip().split(" ", 1)[0].lower()
        kind = title if title in _TYPES[1:] else "feat"
    first = re.split(r"(?<=[.!?])\s", summary, maxsplit=1)[0].rstrip(".")
    return f"{kind}: {first[:72]} ({task.task_id})"


def _commit_and_approve(
    root: Path,
    task: plan.Task,
    base_commit: str,
    round_: int,
    log: Path | None,
    summary: str,
    *,
    empty_if_clean: bool = False,
    echo: bool = False,
    remember: Callable[[str], None] | None = None,
) -> Outcome:
    """The relay itself makes a configured pipeline's one finishing commit (spec 5.6).

    No stage in a configured pipeline may commit -- the same HEAD-check every
    stage's turn is already held to also covers the terminal one, so this
    re-check is defense in depth, not the primary guard: if it ever fires, a
    stage ignored its own rendered prompt's protocol footer (prompts.stage_footer)
    and a bug let it through anyway.

    When that check passes and the task repository has nothing staged, a
    configured pipeline (`empty_if_clean`) still records one empty commit
    naming the task, so a task whose work was already in history can be
    ticked. A legacy two-role run leaves the flag false: the reviewer commits,
    and an approval with no such commit still pauses.
    """
    repo = task_repo(root, task)
    slid = _advance_base_past_settings(repo, task, base_commit, echo=echo)
    if slid is None or gitcheck.head_commit(repo) != slid:
        raise Paused(
            f"{task.task_id}: HEAD moved before the relay could make its own commit; "
            f"a stage committed when it must not have. Undo it with `git reset "
            f"{base_commit[:12]}` (your files stay), then resume",
            log,
        )
    if slid != base_commit and remember is not None:
        remember(slid)
    base_commit = slid
    if not gitcheck.commit_all(repo, commit_message(task, summary)) and empty_if_clean:
        gitcheck.commit_empty(repo, commit_message(task, ""))
    return _approved(root, task, base_commit, round_, log)


def _run_task(
    root: Path,
    settings: config.Config,
    task: plan.Task,
    *,
    base_commit: str,
    echo: bool = True,
    resume: bool = False,
    start_round: int = 1,
    on_turn: Callable[[int, str | None, dict], None] | None = None,
    runner: failover.Runner = subprocess.run,
    extra_prompt: str = "",
    base_holder: list[str] | None = None,
) -> Outcome:
    """Drive one task from implement to an approved, verified commit.

    Raises Paused for every stop condition. Never infers a verdict from an
    agent's exit code or output — only from the handoff record it wrote.

    With `resume`, re-enter at the decision point the handoff record shows,
    not at the start: a task Codex already handed off goes straight to the
    review, one sent back carries its feedback into Codex's next turn, and one
    already approved goes straight to commit verification. A record that names
    another task is ignored. `on_turn` is told the round and the last handoff
    id before every agent turn, so a caller can save resumable state.
    """
    round_ = start_round
    feedback = ""
    next_move = routing.IMPLEMENT
    ephemeral: dict[str, str] = {}
    ephemeral_tried: dict[str, list[str]] = {}

    def remember(commit: str) -> None:
        nonlocal base_commit
        base_commit = commit
        if base_holder is not None:
            base_holder[0] = commit

    implementer = failover.effective_agent(root, settings, "implementer")
    reviewer = failover.effective_agent(root, settings, "reviewer")
    gitcheck.ensure_relay_ignored(root)
    repo = task_repo(root, task)
    whylinecmd.claim(root, task.task_id, implementer, "implementer")
    if resume:
        current = handoff.read(root)
        if current is not None and current.task == task.task_id:
            resumed = routing.decide(
                current,
                None,
                settings.status_map,
                implementer=implementer,
                reviewer=reviewer,
            )
            if resumed in (routing.REVIEW, routing.APPROVED):
                next_move = resumed
            elif (
                resumed == routing.IMPLEMENT
                and current.status == settings.status_map["changes"]
            ):
                feedback = current.summary

    while True:
        implementer = failover.effective_agent(root, settings, "implementer")
        reviewer = failover.effective_agent(root, settings, "reviewer")
        if next_move == routing.APPROVED:
            return _approved(root, task, base_commit, round_, None)
        role = "implementer" if next_move == routing.IMPLEMENT else "reviewer"
        agent = implementer if role == "implementer" else reviewer
        if role in ephemeral:
            agent = ephemeral[role]
            if role == "implementer":
                implementer = agent
            else:
                reviewer = agent
        else:
            ephemeral_tried.pop(role, None)
        template = "implement" if role == "implementer" else "review"
        previous = handoff.read(root)
        previous_id = previous.event_id if previous else None
        head_before = gitcheck.head_commit(repo)
        if on_turn is not None:
            on_turn(round_, previous_id, {})

        target = run_agent(
            root,
            settings,
            agent,
            role,
            template,
            task,
            round_,
            feedback,
            echo,
            implementer=implementer,
            reviewer=reviewer,
            prompt_suffix=extra_prompt,
            runner=runner,
        )
        if role == "implementer" and gitcheck.head_commit(repo) != head_before:
            # Codex's sandbox does not stop it committing (measured on
            # codex-cli 0.155.1); only the prompt says not to, so check.
            # A commit that only edits the relay config is Set up or the
            # console bar saving, not the implementer's work.
            slid = _advance_base_past_settings(repo, task, base_commit, echo=echo)
            if slid is None:
                raise Paused(
                    f"{agent} made a commit, which the relay forbids (only the reviewer "
                    f"commits). Undo it with `git reset {head_before[:12]}` (your files "
                    "stay), then resume",
                    target,
                )
            remember(slid)

        record = handoff.read(root)
        move = routing.decide(
            record,
            previous_id,
            settings.status_map,
            implementer=implementer,
            reviewer=reviewer,
        )

        if move == routing.NO_HANDOFF:
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if _failover_no_handoff(
                root,
                settings,
                agent=agent,
                role=role,
                text=text,
                target=target,
                runner=runner,
                echo=echo,
                ephemeral=ephemeral,
                ephemeral_tried=ephemeral_tried,
            ):
                continue
            adapter = config.adapter_for(settings, agent)
            raise Paused(
                f"{agent} exited without handing off"
                f"{_no_handoff_detail(target, adapter, settings.agents[agent])}; "
                "nothing was routed",
                target,
            )
        ephemeral.pop(role, None)
        ephemeral_tried.pop(role, None)
        if record.task != task.task_id:
            raise Paused(
                f"{agent} handed off for {record.task!r}, but this run is on "
                f"{task.task_id!r}; the relay will not guess",
                target,
            )
        if record.from_actor and record.from_actor.strip().lower() != agent.lower():
            raise Paused(
                f"{agent} recorded its handoff as from {record.from_actor!r}, not {agent!r}. "
                "Check the prompt templates in .whyline/relay/prompts; after changing "
                f"[roles], run `{invocation.command('init')} --overwrite`",
                target,
            )
        if move == routing.BLOCKED:
            raise Paused(_blocked_reason(agent, record), target)
        if move == routing.UNKNOWN:
            raise Paused(
                f"unrecognised handoff status {record.status!r}; the relay will not guess",
                target,
            )
        if move == routing.APPROVED:
            if gitcheck.commit_verified(repo, base_commit, task.task_id):
                return _approved(root, task, base_commit, round_, target)  # an older review.md committed
            return _commit_and_approve(
                root, task, base_commit, round_, target, record.summary,
                echo=echo, remember=remember,
            )
        if move == routing.IMPLEMENT:
            feedback = record.summary
            round_ += 1
            if round_ > settings.max_rounds:
                raise Paused(
                    f"{task.task_id} hit the {settings.max_rounds}-round cap without "
                    "an approval",
                    target,
                )
        next_move = move


def _profile_for(task: plan.Task, pipe: pipeline.Pipeline) -> str:
    """The pipeline profile a task runs.

    `relay-profile: verify` is a task marker. It selects a pipeline profile
    only when one is actually named `verify`. Otherwise the task runs the
    default profile, the same stages as a task that names no profile.
    """
    if task.profile == "verify" and task.profile not in pipe.profiles:
        return pipe.default_profile
    return task.profile or pipe.default_profile


def _run_configured_task(
    root: Path,
    settings: config.Config,
    task: plan.Task,
    *,
    base_commit: str,
    echo: bool = True,
    resume: bool = False,
    start_round: int = 1,
    on_turn: Callable[[int, str | None, dict], None] | None = None,
    runner: failover.Runner = subprocess.run,
    extra_prompt: str = "",
    base_holder: list[str] | None = None,
) -> Outcome:
    """Drive one task through a configured [pipeline], stage by stage.

    Mirrors _run_task's contract and most of its per-turn checks (task
    mismatch, from_actor, blocked, unknown, no-handoff/timeout) but routes on
    pipeline.decide() against the stage that just ran, not a fixed pair of
    roles. On a no-handoff, walks settings.backup_chain for stage.role exactly like
    _run_task's own legacy failover branch, via failover.next_backup.

    With `resume`, state.json's saved stage/profile/stage_visits pick the task
    back up where it paused. State is checkpointed (via `on_turn`) before every
    agent launch, never after: a crash before that checkpoint lands re-enters
    the same turn; a crash after it but before the agent's own handoff write
    also re-enters the same turn (the checkpoint never claims a turn happened
    until it's about to). A crash after the agent already wrote its handoff is
    caught here by re-deciding on whatever handoff already exists before the
    main loop restarts, so it is never re-run and never silently dropped.
    """
    def remember(commit: str) -> None:
        nonlocal base_commit
        base_commit = commit
        if base_holder is not None:
            base_holder[0] = commit

    ephemeral: dict[str, str] = {}
    ephemeral_tried: dict[str, list[str]] = {}
    pipe = settings.pipeline
    effective_agents = {
        name: failover.pipeline_effective_agent(root, settings, name)
        for name in pipe.roles
    }
    round_ = start_round
    feedback = ""
    gitcheck.ensure_relay_ignored(root)
    repo = task_repo(root, task)
    start_profile = _profile_for(task, pipe)
    start_stage_id = pipe.profiles[start_profile].stages[0]
    whylinecmd.claim(
        root,
        task.task_id,
        pipe.roles[pipe.stages[start_stage_id].role].agent,
        pipe.stages[start_stage_id].role,
    )
    saved = state.load(root) if resume else None
    if resume and saved is not None and saved.task_id == task.task_id and saved.stage:
        if saved.pipeline_fingerprint != settings.pipeline_fingerprint:
            raise Paused(
                f"{task.task_id} was paused mid-pipeline, but the pipeline has changed "
                "since then; its saved stage and visit counts no longer match the "
                "configured graph. Resolve by hand before resuming",
                None,
            )
        if saved.stage == "@complete":
            # Approved and checkpointed before a previous pause, but not yet
            # committed: no agent to re-run, only the relay's own commit to retry.
            # The terminal handoff is still on disk -- nothing since has replaced
            # it, since committing it is the very last step of the task.
            current = handoff.read(root)
            if current is None or current.task != task.task_id:
                raise Paused(
                    f"{task.task_id} was approved before a previous pause, but its "
                    "handoff record is now missing or names a different task; the "
                    "relay will not guess the commit message. Resolve by hand",
                    None,
                )
            return _commit_and_approve(
                root, task, base_commit, round_, None, current.summary,
                empty_if_clean=True, echo=echo, remember=remember,
            )
        profile_name = saved.profile
        current_stage_id = saved.stage
        stage_visits = dict(saved.stage_visits)
        current = handoff.read(root)
        if current is not None and current.task == task.task_id:
            resumed = pipeline.decide(
                current,
                None,
                pipe,
                effective_agents,
                current_stage_id=current_stage_id,
                profile_name=profile_name,
            )
            if resumed.kind == "advance":
                # Pause before the visit is counted, or resume would burn one
                # for the stage the refusal was about to be sent back to.
                if _sandbox_backroute(
                    pipe,
                    profile_name,
                    current_stage_id,
                    resumed.target_stage,
                    current.summary,
                ):
                    refused_by = effective_agents[
                        pipe.stages[current_stage_id].role
                    ]
                    raise Paused(
                        _sandbox_pause_reason(refused_by, current.summary), None
                    )
                current_stage_id = resumed.target_stage
                stage_visits[current_stage_id] = (
                    stage_visits.get(current_stage_id, 0) + 1
                )
                check_visit_cap(pipe, current_stage_id, stage_visits, task)
                feedback = current.summary
            elif resumed.kind == "complete":
                return _commit_and_approve(
                    root, task, base_commit, round_, None, current.summary,
                    empty_if_clean=True, echo=echo, remember=remember,
                )
            elif resumed.kind == "blocked":
                raise Paused(
                    _blocked_reason(current.from_actor or "agent", current), None
                )
            # "unknown"/"no-handoff": current_stage_id/stage_visits stand; re-run it.
    else:
        profile_name = start_profile
        current_stage_id = start_stage_id
        stage_visits = {current_stage_id: 1}

    implementer_agent = (
        failover.pipeline_effective_agent(root, settings, "implementer")
        if "implementer" in pipe.roles
        else ""
    )
    reviewer_agent = (
        failover.pipeline_effective_agent(root, settings, "reviewer")
        if "reviewer" in pipe.roles
        else ""
    )
    while True:
        effective_agents = {
            name: failover.pipeline_effective_agent(root, settings, name)
            for name in pipe.roles
        }
        stage = pipe.stages[current_stage_id]
        if stage.role in ephemeral:
            agent = ephemeral[stage.role]
        else:
            agent = effective_agents[stage.role]
            ephemeral_tried.pop(stage.role, None)
        turn_implementer = (
            agent if stage.role == "implementer"
            else effective_agents.get("implementer", implementer_agent)
        )
        turn_reviewer = (
            agent if stage.role == "reviewer"
            else effective_agents.get("reviewer", reviewer_agent)
        )
        previous = handoff.read(root)
        previous_id = previous.event_id if previous else None
        head_before = gitcheck.head_commit(repo)
        stage_state = {
            "profile": profile_name,
            "stage": current_stage_id,
            "stage_visits": dict(stage_visits),
            "pipeline_fingerprint": settings.pipeline_fingerprint,
        }
        if on_turn is not None:
            on_turn(round_, previous_id, stage_state)
        target = run_agent(
            root,
            settings,
            agent,
            stage.role,
            stage.prompt,
            task,
            round_,
            feedback,
            echo,
            implementer=turn_implementer,
            reviewer=turn_reviewer,
            action_label=stage.id,
            log_suffix=stage.id,
            actor=agent,
            stage=current_stage_id,
            profile=profile_name,
            prompt_suffix=extra_prompt + prompts.stage_footer(
                stage, pipe, profile_name, effective_agents, agent, task.task_id
            ),
            runner=runner,
        )
        if gitcheck.head_commit(repo) != head_before:
            slid = _advance_base_past_settings(repo, task, base_commit, echo=echo)
            if slid is None:
                raise Paused(
                    f"{agent} made a commit in stage {current_stage_id!r}, which the relay "
                    "forbids: for a configured pipeline, only the relay itself commits, "
                    f"after every stage approves. Undo it with `git reset "
                    f"{head_before[:12]}` (your files stay), then resume",
                    target,
                )
            remember(slid)
        record = handoff.read(root)
        decision = pipeline.decide(
            record,
            previous_id,
            pipe,
            effective_agents,
            current_stage_id=current_stage_id,
            profile_name=profile_name,
        )
        if decision.kind == "no-handoff":
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            if _failover_no_handoff(
                root,
                settings,
                agent=agent,
                role=stage.role,
                text=text,
                target=target,
                runner=runner,
                echo=echo,
                ephemeral=ephemeral,
                ephemeral_tried=ephemeral_tried,
            ):
                continue
            adapter = config.adapter_for(settings, agent)
            raise Paused(
                f"{agent} exited without handing off"
                f"{_no_handoff_detail(target, adapter, settings.agents[agent])}; "
                "nothing was routed",
                target,
            )
        ephemeral.pop(stage.role, None)
        ephemeral_tried.pop(stage.role, None)
        if record.task != task.task_id:
            raise Paused(
                f"{agent} handed off for {record.task!r}, but this run is on "
                f"{task.task_id!r}; the relay will not guess",
                target,
            )
        if record.from_actor and record.from_actor.strip().lower() != agent.lower():
            raise Paused(
                f"{agent} recorded its handoff as from {record.from_actor!r}, not "
                f"{agent!r}. Check the prompt templates in .whyline/relay/prompts; "
                f"after changing [pipeline] or [roles], run "
                f"`{invocation.command('init')} --overwrite`",
                target,
            )
        if decision.kind == "blocked":
            raise Paused(_blocked_reason(agent, record), target)
        if decision.kind == "unknown":
            raise Paused(
                f"unrecognised outcome {record.status!r} for stage "
                f"{current_stage_id!r}; the relay will not guess",
                target,
            )
        if decision.kind == "complete":
            # Checkpoint the approval before making the relay's own commit: a
            # crash between this save and the commit must not re-run any agent
            # on resume, only retry the commit (see the "@complete" branch above).
            if on_turn is not None:
                on_turn(
                    round_,
                    previous_id,
                    {
                        "profile": profile_name,
                        "stage": "@complete",
                        "stage_visits": dict(stage_visits),
                        "pipeline_fingerprint": settings.pipeline_fingerprint,
                    },
                )
            return _commit_and_approve(
                root, task, base_commit, round_, target, record.summary,
                empty_if_clean=True, echo=echo, remember=remember,
            )
        if _sandbox_backroute(
            pipe,
            profile_name,
            current_stage_id,
            decision.target_stage,
            record.summary,
        ):
            raise Paused(_sandbox_pause_reason(agent, record.summary), target)
        feedback = record.summary
        current_stage_id = decision.target_stage
        stage_visits[current_stage_id] = stage_visits.get(current_stage_id, 0) + 1
        check_visit_cap(pipe, current_stage_id, stage_visits, task)
        round_ += 1
        if round_ > settings.max_rounds:
            raise Paused(
                f"{task.task_id} hit the {settings.max_rounds}-round cap without "
                'reaching "@complete"',
                target,
            )


def run_task(
    root: Path,
    settings: config.Config,
    task: plan.Task,
    *,
    base_commit: str,
    echo: bool = True,
    resume: bool = False,
    start_round: int = 1,
    on_turn: Callable[[int, str | None, dict], None] | None = None,
    _clear_running: bool = True,
    runner: failover.Runner = subprocess.run,
    extra_prompt: str = "",
    base_holder: list[str] | None = None,
) -> Outcome:
    """Drive one task and clear its live marker when used outside ``run_plan``."""
    driver = _run_configured_task if settings.pipeline is not None else _run_task
    try:
        return driver(
            root,
            settings,
            task,
            base_commit=base_commit,
            echo=echo,
            resume=resume,
            start_round=start_round,
            on_turn=on_turn,
            runner=runner,
            extra_prompt=extra_prompt,
            base_holder=base_holder,
        )
    finally:
        if _clear_running:
            running.clear(root)


def stop_path(root: Path) -> Path:
    return config.relay_dir(root) / "STOP"


def stop_requested(root: Path) -> bool:
    return stop_path(root).exists()


# Same slice as agents._CANCEL_POLL_SECONDS: the file is noticed, then the
# child is killed, within about two polls.
_STOP_POLL_SECONDS = 0.05


def _watch_for_stop(
    root: Path, cancel_event: threading.Event, done: threading.Event
) -> None:
    """Set `cancel_event` when the STOP file appears. `done` ends the watch."""
    path = stop_path(root)
    while not done.is_set():
        if path.exists():
            cancel_event.set()
            return
        done.wait(_STOP_POLL_SECONDS)


def _save_pause(
    root: Path,
    plan_path: Path,
    branch: str,
    task: plan.Task,
    base_commit: str,
    reason: str,
    log: Path | None,
    only: str | None,
    progress: dict,
    *,
    timeout_minutes: int,
) -> None:
    stage_state = progress.get("stage_state") or {}
    try:
        state.save(
            root,
            state.RelayState(
                plan=str(plan_path),
                branch=branch,
                task_id=task.task_id,
                round=progress["round"],
                base_commit=base_commit,
                paused_reason=reason,
                log_path=str(log or ""),
                only=only or "",
                last_handoff_id=progress["last"] or "",
                profile=stage_state.get("profile", ""),
                stage=stage_state.get("stage", ""),
                stage_visits=stage_state.get("stage_visits", {}),
                pipeline_fingerprint=stage_state.get("pipeline_fingerprint", ""),
                timeout_minutes=timeout_minutes,
            ),
        )
    except state.DiskFull as error:
        _die_disk_full(error)


DECISION_LOG = Path(".whyline") / "decisions.md"


def _verify_test_plan(plan_path: Path, task: plan.Task) -> Path | None:
    """The test plan a verify task carries, or None when the task is ordinary.

    A verify task with no test plan beside its plan is an ordinary task: no
    scenario prompt, and the tick commit does not look for a test plan.
    """
    if task.profile != "verify":
        return None
    path = testplan.path_for(plan_path)
    return path if path.is_file() else None


def _verify_prompt(root: Path, plan_path: Path, task: plan.Task) -> str:
    """Automatic scenarios for a verify task, already limited to its phase."""
    path = _verify_test_plan(plan_path, task)
    if path is None:
        return ""
    tasks = plan.parse(plan_path.read_text(encoding="utf-8"))
    in_range = set(plan.ids_since_previous_release(tasks, task.task_id))
    scenarios = [
        scenario
        for scenario in testplan.parse(path.read_text(encoding="utf-8"))
        if scenario.kind == "auto" and in_range.intersection(scenario.covers)
    ]
    try:
        shown = str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        shown = str(path)
    return prompts.verify_task_addendum(scenarios, test_plan=shown)


def _require_clean(root: Path, plan_path: Path, task: plan.Task) -> None:
    """After an approval nothing may be left uncommitted, or it rides into the next task.

    The plan file is exempt: it is ticked and committed straight afterwards.
    For a task in another repository, so is the relay repository's decision
    log, which agents append to there and the tick commit carries. A verify
    task's test plan is exempt for the same reason when it is still dirty:
    the task repository's finishing commit cannot see a file that lives
    beside the plan.
    """
    try:
        own = str(plan_path.resolve().relative_to(root.resolve()))
    except ValueError:
        own = None
    repo = task_repo(root, task)
    exempt = {own}
    if repo != root.resolve():
        exempt.add(str(DECISION_LOG))
    test_plan = _verify_test_plan(plan_path, task)
    if test_plan is not None:
        try:
            exempt.add(str(test_plan.resolve().relative_to(root.resolve())))
        except ValueError:
            pass
    leftovers = [path for path in gitcheck.dirty_paths(root) if path not in exempt]
    if repo != root.resolve():
        leftovers += [str(repo / path) for path in gitcheck.dirty_paths(repo)]
    if leftovers:
        raise Paused(
            f"{task.task_id} was approved and committed, but files are still "
            f"uncommitted ({', '.join(leftovers[:5])}). Stash or remove them (do not "
            "commit them: the relay verifies the last commit's message), then resume, "
            "or resume with --allow-dirty."
        )


def _enter_task_repo(repo: Path, branch: str, task: plan.Task, *, fresh: bool) -> None:
    """Put another repository on the relay's branch before a task works in it.

    A fresh task refuses to start on uncommitted changes there, the same rule
    the relay applies to its own repository at start: they would ride into the
    task's commit. A resumed task's own work in progress is expected.
    """
    if fresh:
        dirty = gitcheck.dirty_paths(repo)
        if dirty:
            raise Paused(
                f"{task.task_id}: {repo} has uncommitted changes "
                f"({', '.join(dirty[:5])}). Commit or stash them there, then resume"
            )
    if gitcheck.current_branch(repo) != branch:
        gitcheck.ensure_branch(repo, branch)


def _tick_and_commit(root: Path, plan_path: Path, task: plan.Task) -> None:
    """Tick the box and commit that one file, so the tree is clean after every approval.

    Re-reads the plan first: an agent may have edited it while it worked, and
    ticking a copy read before the run would silently overwrite that edit.
    A verify task also commits its test plan when that file still has changes.
    The finishing commit already includes it when it lives in the task
    repository; this catches the copy that lives beside the plan instead.
    """
    current = plan_path.read_text(encoding="utf-8")
    plan_path.write_text(plan.tick(current, task.task_id), encoding="utf-8")
    paths = [plan_path]
    if task_repo(root, task) != root.resolve():
        paths.append(root / DECISION_LOG)
    test_plan = _verify_test_plan(plan_path, task)
    if test_plan is not None:
        paths.append(test_plan)
    gitcheck.commit_paths(root, paths, f"chore: tick {task.task_id} in the plan")


def _paused_release(root: Path, task_id: str) -> state.RelayState:
    saved = state.load(root)
    if saved is None or not saved.paused_reason.startswith(RELEASE_PREFIX):
        raise ValueError("the relay is not paused on a release task")
    if saved.task_id != task_id:
        raise ValueError(f"the relay is paused on release task {saved.task_id}, not {task_id}")
    return saved


def mark_done(root: Path, task_id: str) -> None:
    saved = _paused_release(root, task_id)
    gitcheck.ensure_relay_ignored(root)
    if saved.branch:
        gitcheck.ensure_branch(root, saved.branch)
    plan_path = Path(saved.plan) if Path(saved.plan).is_absolute() else root / saved.plan
    current = plan_path.read_text(encoding="utf-8")
    if plan.find(plan.parse(current), task_id) is None:
        raise ValueError(f"{task_id} is no longer in {plan_path.name}")
    plan_path.write_text(plan.tick(current, task_id), encoding="utf-8")
    gitcheck.commit_paths(root, [plan_path], f"chore: release task {task_id} done by hand")
    state.clear(root)


def mark_skipped(root: Path, task_id: str) -> None:
    saved = _paused_release(root, task_id)
    gitcheck.ensure_relay_ignored(root)
    if saved.branch:
        gitcheck.ensure_branch(root, saved.branch)
    skipped = config.relay_dir(root) / "skipped-tasks.json"
    ids = json.loads(skipped.read_text(encoding="utf-8")) if skipped.exists() else []
    if task_id not in ids:
        ids.append(task_id)
    skipped.parent.mkdir(parents=True, exist_ok=True)
    skipped.write_text(json.dumps(ids), encoding="utf-8")
    state.clear(root)


def _skipped_tasks(root: Path) -> list[str]:
    skipped = config.relay_dir(root) / "skipped-tasks.json"
    if skipped.exists():
        try:
            data = json.loads(skipped.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(item) for item in data]
        except Exception:
            pass
    return []


def _with_release_implementer(settings: config.Config, release_agent: str) -> config.Config:
    if settings.pipeline is None:
        return dataclasses.replace(
            settings, roles=config.Roles(release_agent, settings.roles.reviewer)
        )
    pipeline = settings.pipeline
    roles = dict(pipeline.roles)
    if "implementer" in roles:
        roles["implementer"] = dataclasses.replace(roles["implementer"], agent=release_agent)
    return dataclasses.replace(settings, pipeline=dataclasses.replace(pipeline, roles=roles))


class ManualCheckError(ValueError):
    """A manual-check command cannot be applied to this pause."""


def _plan_file(root: Path, saved: state.RelayState) -> Path:
    path = Path(saved.plan)
    return path if path.is_absolute() else root / path


def _paused_release_state(root: Path) -> state.RelayState | None:
    """The saved pause when it is a release, or manual checks before one."""
    saved = state.load(root)
    if saved is None:
        return None
    reason = saved.paused_reason
    if reason.startswith(testplan.MANUAL_PREFIX) or reason.startswith(RELEASE_PREFIX):
        return saved
    return None


def _open_manual_checks(
    plan_path: Path, tasks: list[plan.Task], task_id: str
) -> list[testplan.Scenario]:
    """Unchecked manual scenarios for the tasks since the previous release.

    No test plan beside the plan means there is nothing to confirm.
    """
    test_path = testplan.path_for(plan_path)
    if not test_path.is_file():
        return []
    task_ids = plan.ids_since_previous_release(tasks, task_id)
    return testplan.open_manual(test_path.read_text(encoding="utf-8"), task_ids)


def manual_checks(root: Path) -> tuple[state.RelayState, list[testplan.Scenario]] | None:
    """The paused release and the manual checks still open for it.

    None when this pause is not a release and not manual checks before one.
    """
    saved = _paused_release_state(root)
    if saved is None:
        return None
    plan_path = _plan_file(root, saved)
    if not plan_path.is_file():
        return saved, []
    tasks = plan.parse(plan_path.read_text(encoding="utf-8"))
    return saved, _open_manual_checks(plan_path, tasks, saved.task_id)


def manual_refusal(root: Path) -> str | None:
    """The open-check list when `done` or `resume` must refuse, else None."""
    found = manual_checks(root)
    if found is None:
        return None
    saved, pending = found
    if not pending:
        return None
    return (
        "Refusing while manual checks are open:\n"
        + testplan.pause_reason(saved.task_id, pending)
    )


def record_manual(
    root: Path,
    scenario_id: str,
    *,
    passed: bool,
    note: str | None = None,
) -> list[testplan.Scenario]:
    """Tick or fail one open manual check and commit only the test plan.

    Returns the checks still open afterwards. The release stays paused.
    """
    saved = _paused_release_state(root)
    if saved is None:
        raise ManualCheckError("the relay is not paused before a release")
    gitcheck.ensure_relay_ignored(root)
    if saved.branch:
        gitcheck.ensure_branch(root, saved.branch)
    plan_path = _plan_file(root, saved)
    if not plan_path.is_file():
        raise ManualCheckError(f"no plan at {plan_path}")
    test_path = testplan.path_for(plan_path)
    if not test_path.is_file():
        raise ManualCheckError(f"no test plan beside {plan_path.name}")
    tasks = plan.parse(plan_path.read_text(encoding="utf-8"))
    text = test_path.read_text(encoding="utf-8")
    task_ids = plan.ids_since_previous_release(tasks, saved.task_id)
    open_ids = {scenario.id for scenario in testplan.open_manual(text, task_ids)}
    if scenario_id not in open_ids:
        raise ManualCheckError(
            f"{scenario_id} is not an open manual check for {saved.task_id}"
        )
    if passed:
        updated = testplan.mark_passed(text, scenario_id, note)
        message = f"test: confirm {scenario_id}"
    else:
        if note is None or not note.strip():
            raise ManualCheckError("fail needs a note")
        updated = testplan.mark_failed(text, scenario_id, note)
        message = f"test: fail {scenario_id}"
    test_path.write_text(updated, encoding="utf-8")
    if not gitcheck.commit_paths(root, [test_path], message):
        raise ManualCheckError(
            f"{test_path.name} did not change, so nothing was committed"
        )
    remaining = testplan.open_manual(test_path.read_text(encoding="utf-8"), task_ids)
    if saved.paused_reason.startswith(testplan.MANUAL_PREFIX):
        state.save(
            root,
            dataclasses.replace(
                saved, paused_reason=testplan.pause_reason(saved.task_id, remaining)
            ),
        )
    return remaining


def _run_plan(
    root: Path,
    settings: config.Config,
    plan_path: Path,
    *,
    branch: str,
    only: str | None = None,
    resume: bool = False,
    allow_dirty: bool = False,
    echo: bool = True,
    runner: failover.Runner = subprocess.run,
) -> list[Outcome]:
    """Run every unchecked task in file order. Tick each only after it commits.

    With `resume`, the first task re-enters where it stopped: it reuses the base
    commit saved when it paused (the reviewer may already have committed, and a
    fresh base would make the approval unverifiable) and the round it was on,
    and `run_task` picks the next agent from the handoff record.
    """
    gitcheck.ensure_relay_ignored(root)
    outcomes: list[Outcome] = []
    resuming = state.load(root) if resume else None
    while True:
        if stop_requested(root):
            print("STOP file present; not starting another task.")
            return outcomes
        tasks = plan.parse(plan_path.read_text(encoding="utf-8"))
        skipped_ids = _skipped_tasks(root)
        if only:
            task = plan.find(tasks, only)
            if task is None:
                raise plan.PlanError(f"no task {only!r} in the plan")
        else:
            task = plan.next_unchecked(tasks, skip=skipped_ids)
        if task is None or task.checked or task.task_id in skipped_ids:
            state.clear(root)
            return outcomes

        # Manual checks gate every release, whoever runs it, before it
        # starts. An empty base means it has not started, so resume comes
        # back through this gate. A pause that already has a base is the
        # release itself and continues.
        release_not_started = not (
            resuming is not None
            and resuming.task_id == task.task_id
            and bool(resuming.base_commit)
        )
        if task.profile == "release" and release_not_started:
            pending = _open_manual_checks(plan_path, tasks, task.task_id)
            if pending:
                reason = testplan.pause_reason(task.task_id, pending)
                gitcheck.ensure_relay_ignored(root)
                _save_pause(
                    root,
                    plan_path,
                    branch,
                    task,
                    "",
                    reason,
                    None,
                    only,
                    {"round": 1, "last": None},
                    timeout_minutes=settings.timeout_minutes,
                )
                raise Paused(reason)

        if task.profile == "release" and settings.release_role == "human":
            checklist = "\n".join(l.strip() for l in task.text.splitlines()[1:]
                                  if l.strip() and not l.strip().startswith("relay-profile:"))
            reason = f"{RELEASE_PREFIX}{task.task_id}\n{checklist}" if checklist else f"{RELEASE_PREFIX}{task.task_id}"
            gitcheck.ensure_relay_ignored(root)
            _save_pause(root, plan_path, branch, task, gitcheck.head_commit(task_repo(root, task)),
                        reason, None, only, {"round": 1, "last": None},
                        timeout_minutes=settings.timeout_minutes)
            raise Paused(reason)
        if task.profile == "release":  # an agent does releases
            task_settings = _with_release_implementer(settings, settings.release_role)
            task = dataclasses.replace(task, profile=None)
        else:
            task_settings = settings

        # A pause saved before the task took its base commit (its repository
        # was dirty) has no base: resuming it starts the task fresh.
        mid_task = (
            resuming is not None
            and resuming.task_id == task.task_id
            and bool(resuming.base_commit)
        )
        start_round = (resuming.round or 1) if mid_task else 1
        progress = {"round": start_round, "last": None}
        try:
            repo = task_repo(root, task)
            if repo != root.resolve():
                _enter_task_repo(repo, branch, task, fresh=not mid_task)
        except Paused as paused:
            _save_pause(
                root, plan_path, branch, task,
                resuming.base_commit if mid_task else "",
                paused.reason, None, only, progress,
                timeout_minutes=settings.timeout_minutes,
            )
            raise
        base_commit = resuming.base_commit if mid_task else gitcheck.head_commit(repo)
        base_holder = [base_commit]
        resuming = None

        def on_turn(round_: int, previous_id: str | None, stage_state: dict) -> None:
            progress["round"] = round_
            progress["last"] = previous_id
            progress["stage_state"] = stage_state

        try:
            outcome = run_task(
                root,
                task_settings,
                task,
                base_commit=base_commit,
                echo=echo,
                resume=mid_task,
                start_round=start_round,
                on_turn=on_turn,
                _clear_running=False,
                runner=runner,
                extra_prompt=_verify_prompt(root, plan_path, task),
                base_holder=base_holder,
            )
            if not allow_dirty:
                _require_clean(root, plan_path, task)
        except state.DiskFull as error:
            _die_disk_full(error)
        except Paused as paused:
            _save_pause(
                root, plan_path, branch, task, base_holder[0],
                paused.reason, paused.log_path, only, progress,
                timeout_minutes=settings.timeout_minutes,
            )
            raise
        except KeyboardInterrupt:
            _save_pause(
                root, plan_path, branch, task, base_holder[0],
                "interrupted by the user (Ctrl+C)", None, only, progress,
                timeout_minutes=settings.timeout_minutes,
            )
            raise

        _tick_and_commit(root, plan_path, task)
        if echo:
            agents.print_status(f"==> relay: ticked {task.task_id} in the plan")
        outcomes.append(outcome)
        if only:
            state.clear(root)
            return outcomes


def run_plan(
    root: Path,
    settings: config.Config,
    plan_path: Path,
    *,
    branch: str,
    only: str | None = None,
    resume: bool = False,
    allow_dirty: bool = False,
    echo: bool = True,
    runner: failover.Runner = subprocess.run,
) -> list[Outcome]:
    """Run a plan while retaining one live marker across all of its tasks."""
    try:
        return _run_plan(
            root,
            settings,
            plan_path,
            branch=branch,
            only=only,
            resume=resume,
            allow_dirty=allow_dirty,
            echo=echo,
            runner=runner,
        )
    finally:
        running.clear(root)
