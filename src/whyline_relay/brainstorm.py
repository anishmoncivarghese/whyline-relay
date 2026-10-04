"""Multi-model brainstorming: independent research, combined review passes,
then one model's final synthesis -- all built on ordinary chat turns."""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from whyline_relay import agents, chat, config, gitcheck, plan

MODEL_OPTIONS = (
    ("1", "claude", "Claude"),
    ("2", "codex", "Codex"),
    ("3", "antigravity", "Antigravity"),
    ("4", "grok", "Grok"),
)
MODEL_OPTIONS_BY_KEY = {key: label for _, key, label in MODEL_OPTIONS}
MODEL_OPTIONS_BY_KEY["agy"] = "Antigravity"

# Lifecycle of one agent turn. `running` is the in-progress state a future
# TUI renders while the turn blocks; the CLI prints only the start line and
# the terminal line.
PROGRESS_STATUSES = ("starting", "running", "succeeded", "failed", "skipped")
PHASE_PASS_ZERO = "pass-zero"
PHASE_REVIEW = "review"
PHASE_SYNTHESIS = "synthesis"
@dataclass(frozen=True)
class AgentStatus:
    """Explicit status representation for an agent in a brainstorm phase.

    Stores the lifecycle status ('succeeded', 'failed', 'skipped') and classified
    reason ('quota/rate-limit', 'timeout', etc., or None on success) separately.
    """

    status: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in PROGRESS_STATUSES:
            raise ValueError(f"unknown brainstorm progress status {self.status!r}")

    def __getitem__(self, key: str) -> Any:
        if key == "status":
            return self.status
        if key == "reason":
            return self.reason
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "status":
            return self.status
        if key == "reason":
            return self.reason
        return default

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentStatus":
        return cls(status=data["status"], reason=data.get("reason"))


TurnStatus = AgentStatus

FAILURE_RATE_LIMIT = "quota/rate-limit"
FAILURE_TIMEOUT = "timeout"
FAILURE_AUTH = "authentication"
FAILURE_PERMISSION = "permission"
FAILURE_MISSING = "missing executable"
FAILURE_GENERIC = "generic non-zero failure"

FAILURE_CATEGORIES = (
    FAILURE_RATE_LIMIT,
    FAILURE_TIMEOUT,
    FAILURE_AUTH,
    FAILURE_PERMISSION,
    FAILURE_MISSING,
    FAILURE_GENERIC,
)

TURN_FAILURE_REASON = "turn reported a failure"


def classify_failure(
    record: dict | None = None,
    error: BaseException | None = None,
) -> str:
    """Classify runtime failures from a run_turn record or an exception into
    one of the canonical categories:
    - "quota/rate-limit"
    - "timeout"
    - "authentication"
    - "permission"
    - "missing executable"
    - "generic non-zero failure"
    """
    if error is not None:
        if isinstance(error, (agents.AgentTimeout, subprocess.TimeoutExpired, TimeoutError)):
            return FAILURE_TIMEOUT
        if isinstance(error, (agents.AgentMissing, chat.AgentUnavailable, FileNotFoundError)):
            return FAILURE_MISSING
        if isinstance(error, PermissionError):
            return FAILURE_PERMISSION
        err_msg = str(error).lower()
        if any(marker in err_msg for marker in agents.RATE_LIMIT_MARKERS) or "quota" in err_msg or "429" in err_msg:
            return FAILURE_RATE_LIMIT
        if any(m in err_msg for m in ("timed out", "timeout", "terminated")):
            return FAILURE_TIMEOUT
        if any(m in err_msg for m in ("not installed", "not on path", "command not found", "no such file")):
            return FAILURE_MISSING
        if any(m in err_msg for m in ("permission denied", "not permitted", "forbidden", "permission policy")):
            return FAILURE_PERMISSION
        if any(m in err_msg for m in ("not logged in", "authentication", "unauthorized", "login")):
            return FAILURE_AUTH
        return FAILURE_GENERIC

    if record is not None and not record.get("ok", True):
        if record.get("rate_limited"):
            return FAILURE_RATE_LIMIT

        notice = (record.get("failover_notice") or "").lower()
        if "hit a usage or rate limit" in notice or "rate limit" in notice or "quota" in notice:
            return FAILURE_RATE_LIMIT
        if "is no longer logged in" in notice or "logged in" in notice or "auth" in notice:
            return FAILURE_AUTH

        response = str(record.get("response") or "")
        raw = str(record.get("raw") or "")
        text = f"{response}\n{raw}".lower()

        if agents.rate_limited(text) or any(
            m in text
            for m in (
                "quota",
                "429",
                "too many requests",
                "resource_exhausted",
                "credit balance",
                "usage limit",
                "rate limit",
            )
        ):
            return FAILURE_RATE_LIMIT

        if any(
            m in text
            for m in (
                "timed out",
                "timeout",
                "exceeded",
                "and was terminated",
            )
        ):
            return FAILURE_TIMEOUT

        if any(
            m in text
            for m in (
                "not logged in",
                "is no longer logged in",
                "login required",
                "please log in",
                "please sign in",
                "sign-in required",
                "authentication",
                "unauthorized",
                "invalid api key",
                "invalid_api_key",
                "credentials",
            )
        ):
            return FAILURE_AUTH

        if any(
            m in text
            for m in (
                "permission denied",
                "denied permission",
                "permission policy",
                "permission_denials",
                "forbidden",
                "access denied",
                "stopreason\": \"cancelled\"",
                "operation not permitted",
            )
        ):
            return FAILURE_PERMISSION

        if any(
            m in text
            for m in (
                "is not installed",
                "not installed or not on path",
                "command not found",
                "no such file or directory",
                "not configured for chat",
                "is not configured",
            )
        ):
            return FAILURE_MISSING

        return FAILURE_GENERIC

    return FAILURE_GENERIC


def _one_line(text: str) -> str:
    """The detail worth showing: a JSON result's text, else the last
    non-blank line; printable characters only, at most 160."""
    from whyline_relay.adapters.base import json_object

    parsed = json_object(text)
    if isinstance(parsed, dict):
        for key in ("result", "error", "message"):
            if isinstance(parsed.get(key), str) and parsed[key].strip():
                text = parsed[key]
                break
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return "".join(ch for ch in lines[-1].strip() if ch.isprintable())[:160].strip()


def failure_reason(
    record: dict | None = None,
    error: BaseException | None = None,
) -> str:
    """classify_failure's category plus the agent's own words, so a usage
    limit or a crash reads as itself instead of "generic non-zero failure"."""
    category = classify_failure(record=record, error=error)
    if error is not None:
        detail = _one_line(str(error))
    else:
        detail = _one_line(str((record or {}).get("response") or "")) or _one_line(
            str((record or {}).get("raw") or "")
        )
    return f"{category} — {detail}" if detail and detail != category else category


@dataclass(frozen=True)
class ProgressEvent:
    """One structured update for a brainstorm agent turn.

    Every field is always present (`pass_number` and `reason` are null when
    they do not apply) so a later TUI can serialize the event with
    dataclasses.asdict and rely on the keys.
    """

    status: str
    agent: str
    label: str
    phase: str
    ordinal: int
    total: int
    elapsed_seconds: float
    pass_number: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in PROGRESS_STATUSES:
            raise ValueError(f"unknown brainstorm progress status {self.status!r}")

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "agent": self.agent,
            "label": self.label,
            "phase": self.phase,
            "ordinal": self.ordinal,
            "total": self.total,
            "elapsed_seconds": self.elapsed_seconds,
            "pass_number": self.pass_number,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProgressEvent":
        return cls(
            status=data["status"],
            agent=data["agent"],
            label=data["label"],
            phase=data["phase"],
            ordinal=data["ordinal"],
            total=data["total"],
            elapsed_seconds=float(data["elapsed_seconds"]),
            pass_number=data.get("pass_number"),
            reason=data.get("reason"),
        )


def format_progress_line(event: ProgressEvent) -> str:
    """Human line for a start or terminal status.

    Includes the 1-based ordinal within this phase, the model label, and
    the compact elapsed time. `running` is not printed.
    """
    elapsed = agents.format_duration(event.elapsed_seconds)
    line = (
        f"[{event.ordinal}/{event.total}] {event.label} "
        f"{event.status} {_progress_scope(event)} ({elapsed})"
    )
    if event.reason:
        return f"{line}: {event.reason}"
    return line


def _progress_scope(event: ProgressEvent) -> str:
    if event.phase == PHASE_REVIEW:
        number = event.pass_number if event.pass_number is not None else "?"
        return f"review pass {number}"
    if event.phase == PHASE_SYNTHESIS:
        return "final synthesis"
    if event.phase == PHASE_PASS_ZERO:
        return "pass-zero"
    return event.phase


def format_progress_table(
    events: Sequence[ProgressEvent | dict[str, Any]],
    *,
    deduplicate: bool = True,
) -> str:
    """Render a compact table showing agent, phase, state, elapsed time, and failure reason.

    Accepts either ProgressEvent instances or serialized dict payloads for future-TUI
    compatibility. By default, consolidates multiple lifecycle events for the same turn
    into its latest state.
    """
    if not events:
        return ""

    parsed_events: list[ProgressEvent] = []
    for item in events:
        if isinstance(item, ProgressEvent):
            parsed_events.append(item)
        elif isinstance(item, dict):
            parsed_events.append(ProgressEvent.from_dict(item))
        else:
            raise TypeError(f"expected ProgressEvent or dict, got {type(item).__name__}")

    if deduplicate:
        turns: dict[tuple[str, int | None, str], ProgressEvent] = {}
        for ev in parsed_events:
            key = (ev.phase, ev.pass_number, ev.agent)
            turns[key] = ev
        rows_to_render = list(turns.values())
    else:
        rows_to_render = parsed_events

    headers = ("Agent", "Phase", "State", "Elapsed Time", "Failure Reason")
    data_rows: list[tuple[str, str, str, str, str]] = []
    for ev in rows_to_render:
        agent_display = ev.label or ev.agent
        phase_display = _progress_scope(ev)
        state_display = ev.status
        elapsed_display = agents.format_duration(ev.elapsed_seconds)
        reason_display = ev.reason if ev.reason else "-"
        data_rows.append((
            agent_display,
            phase_display,
            state_display,
            elapsed_display,
            reason_display,
        ))

    col_widths = [
        max(len(h), max((len(r[i]) for r in data_rows), default=0))
        for i, h in enumerate(headers)
    ]

    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, col_widths)).rstrip()
    sep_line = "  ".join("-" * w for w in col_widths).rstrip()
    lines = [header_line, sep_line]
    for row in data_rows:
        line = "  ".join(val.ljust(w) for val, w in zip(row, col_widths)).rstrip()
        lines.append(line)

    return "\n".join(lines)


def render_progress_table(
    events: Sequence[ProgressEvent | dict[str, Any]],
    print_fn: Callable[..., Any] | None = None,
    *,
    deduplicate: bool = True,
) -> None:
    """Render and print the compact progress table using print_fn."""
    print_fn = print_fn if print_fn is not None else print
    table = format_progress_table(events, deduplicate=deduplicate)
    if table:
        print_fn(table)


class _TurnWatch:
    """Emits starting, running, then one terminal status for a single turn."""

    def __init__(
        self,
        *,
        print_fn,
        progress_fn: Callable[[ProgressEvent], None] | None,
        agent: str,
        label: str,
        phase: str,
        ordinal: int,
        total: int,
        pass_number: int | None,
    ) -> None:
        self._print_fn = print_fn
        self._progress_fn = progress_fn
        self._agent = agent
        self._label = label
        self._phase = phase
        self._ordinal = ordinal
        self._total = total
        self._pass_number = pass_number
        self._started = time.monotonic()

    def emit(self, status: str, reason: str | None = None) -> None:
        elapsed = (
            0.0
            if status == "starting"
            else max(0.0, time.monotonic() - self._started)
        )
        event = ProgressEvent(
            status=status,
            agent=self._agent,
            label=self._label,
            phase=self._phase,
            ordinal=self._ordinal,
            total=self._total,
            elapsed_seconds=elapsed,
            pass_number=self._pass_number,
            reason=reason,
        )
        if status != "running":
            self._print_fn(format_progress_line(event))
        if self._progress_fn is not None:
            self._progress_fn(event)


def _begin_turn(
    *,
    print_fn,
    progress_fn: Callable[[ProgressEvent], None] | None,
    agent: str,
    label: str,
    phase: str,
    ordinal: int,
    total: int,
    pass_number: int | None = None,
) -> _TurnWatch:
    watch = _TurnWatch(
        print_fn=print_fn,
        progress_fn=progress_fn,
        agent=agent,
        label=label,
        phase=phase,
        ordinal=ordinal,
        total=total,
        pass_number=pass_number,
    )
    watch.emit("starting")
    watch.emit("running")
    return watch


def _emit_outcome(watch: _TurnWatch, record: dict) -> None:
    if record["ok"]:
        watch.emit("succeeded")
        return
    reason = failure_reason(record=record)
    watch.emit("failed", reason=reason)


def _model_label(models: list[tuple[str, str]], agent_key: str) -> str:
    for key, label in models:
        if key == agent_key:
            return label
    return MODEL_OPTIONS_BY_KEY.get(agent_key, agent_key)
TIMEOUT_OPTIONS = (15, 30, 45, 60)
DEFAULT_TIMEOUT_MINUTES = 15
DEFAULT_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_MINUTES * 60


def parse_timeout_selection(raw: str, default: int = DEFAULT_TIMEOUT_MINUTES) -> int | None:
    """Parses user input for timeout menu into minutes (15, 30, 45, or 60).

    Returns None if input names nothing valid.
    """
    raw = raw.strip().lower()
    if not raw:
        return default
    cleaned = re.sub(r"\s*(minutes|minute|mins|min|m)$", "", raw).strip()
    if cleaned in {"15", "30", "45", "60"}:
        return int(cleaned)
    menu_numbers = {"1": 15, "2": 30, "3": 45, "4": 60}
    if cleaned in menu_numbers:
        return menu_numbers[cleaned]
    return None


def slugify(topic: str) -> str:
    lowered = topic.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug[:60] or "topic"


def parse_model_selection(raw: str) -> list[tuple[str, str]] | None:
    """Parses "1,2,4" or "5" (all) into [(agent_key, label), ...], in
    MODEL_OPTIONS' own order. None if the input names nothing valid."""
    raw = raw.strip()
    if not raw:
        return None
    if raw == "5":
        return [(key, label) for _, key, label in MODEL_OPTIONS]
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    by_number = {number: (key, label) for number, key, label in MODEL_OPTIONS}
    chosen: list[tuple[str, str]] = []
    for token in tokens:
        if token not in by_number:
            return None
        pair = by_number[token]
        if pair not in chosen:
            chosen.append(pair)
    return chosen or None


def check_availability(
    settings: "config.Config", models: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """The subset of `models` chat.resolve_command would refuse right now --
    checked before any turn runs (spec B6)."""
    unavailable = []
    for agent_key, label in models:
        try:
            chat.resolve_command(settings, agent_key)
        except chat.AgentUnavailable:
            unavailable.append((agent_key, label))
    return unavailable


def _timeout_path(root: Path, topic: str) -> Path:
    return config.relay_dir(root) / "brainstorm-tmp" / f".timeout-{slugify(topic)}.json"


def _load_timeout(root: Path, topic: str) -> int | None:
    path = _timeout_path(root, topic)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return int(data.get("timeout_seconds", data.get("timeout_minutes", 0) * 60))
            return int(data)
        except Exception:
            pass
    return None


def _save_timeout(root: Path, timeout_seconds: int, topic: str) -> None:
    path = _timeout_path(root, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timeout_seconds": timeout_seconds,
        "timeout_minutes": timeout_seconds // 60,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


load_timeout = _load_timeout
save_timeout = _save_timeout
timeout_path = _timeout_path


def ask_brainstorm_setup(
    root: Path,
    settings: "config.Config",
    *,
    input_fn=None,
    print_fn=None,
) -> dict | None:
    """Asks topic/models/passes/final-model/timeout, validating and reprompting.
    Returns {"topic", "models", "passes", "final_agent", "timeout_seconds", "timeout_minutes"},
    or None if the user declined to proceed after an availability warning.
    """
    input_fn = input_fn if input_fn is not None else input
    print_fn = print_fn if print_fn is not None else print

    topic = ""
    while not topic:
        topic = input_fn("What should we research? ").strip()

    models: list[tuple[str, str]] | None = None
    while models is None:
        menu = ", ".join(f"{n} {label}" for n, _, label in MODEL_OPTIONS)
        raw = input_fn(f"Which models? ({menu}, 5 all): ").strip()
        models = parse_model_selection(raw)
        if models is None:
            print_fn(
                "Not understood -- use comma-separated numbers, e.g. 1,2, "
                "or 5 for all."
            )

    passes = None
    while passes is None:
        raw_passes = input_fn("How many passes? [1]: ").strip()
        if not raw_passes:
            passes = 1
        elif raw_passes.isdigit():
            passes = int(raw_passes)
        else:
            print_fn("Enter a whole number of passes (0 or more).")

    valid_keys = {key for key, _ in models}
    default_final = models[0][0]
    final_agent = None
    while final_agent is None:
        raw_final = (
            input_fn(
                f"Which model gives the final synthesis? [{default_final}]: "
            ).strip()
            or default_final
        )
        if raw_final in valid_keys:
            final_agent = raw_final
        else:
            print_fn(
                f"{raw_final} wasn't one of the models you selected -- pick "
                f"one of: {', '.join(valid_keys)}"
            )

    persisted_timeout = _load_timeout(root, topic)
    if persisted_timeout is not None and (persisted_timeout // 60) in TIMEOUT_OPTIONS:
        default_timeout = persisted_timeout // 60
    else:
        default_timeout = DEFAULT_TIMEOUT_MINUTES

    timeout_seconds = None
    timeout_minutes = None
    while timeout_seconds is None:
        raw_timeout = input_fn(
            f"Per-agent timeout: 15 (default), 30, 45, 60 minutes [{default_timeout}]: "
        ).strip()
        parsed_min = parse_timeout_selection(raw_timeout, default=default_timeout)
        if parsed_min is not None:
            timeout_minutes = parsed_min
            timeout_seconds = parsed_min * 60
        else:
            print_fn("Enter a valid timeout: 15 (default), 30, 45, or 60 minutes.")

    unavailable = check_availability(settings, models)
    if unavailable:
        names = ", ".join(label for _, label in unavailable)
        pronoun = "them" if len(unavailable) > 1 else "it"
        proceed = (
            input_fn(
                f"{names} not configured for chat in this repo. Proceed "
                f"without {pronoun}? [y/N]: "
            )
            .strip()
            .lower()
        )
        if proceed != "y":
            return None
        unavailable_keys = {key for key, _ in unavailable}
        models = [(key, label) for key, label in models if key not in unavailable_keys]
        if not models:
            print_fn("No models left to brainstorm with.")
            return None
        if final_agent in unavailable_keys:
            final_agent = models[0][0]
            print_fn(f"Final synthesis will come from {models[0][1]} instead.")

    _save_timeout(root, timeout_seconds, topic)
    return {
        "topic": topic,
        "models": models,
        "passes": passes,
        "final_agent": final_agent,
        "timeout_seconds": timeout_seconds,
        "timeout_minutes": timeout_minutes,
    }


def temp_path(root: Path, agent: str) -> Path:
    return config.relay_dir(root) / "brainstorm-tmp" / f"{agent}.md"


def shared_path(root: Path, topic: str) -> Path:
    return root / "docs" / "brainstorm" / f"{slugify(topic)}.md"


def _actual_agents_path(root: Path, topic: str) -> Path:
    return config.relay_dir(root) / "brainstorm-tmp" / f".actual-agents-{slugify(topic)}.json"


def _load_actual_agents(root: Path, topic: str) -> dict[str, str]:
    path = _actual_agents_path(root, topic)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_actual_agents(root: Path, mapping: dict[str, str], topic: str) -> None:
    path = _actual_agents_path(root, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping), encoding="utf-8")


def _status_path(root: Path, topic: str) -> Path:
    return config.relay_dir(root) / "brainstorm-tmp" / f".status-{slugify(topic)}.json"


def _load_status_map(root: Path, topic: str) -> dict[str, AgentStatus]:
    path = _status_path(root, topic)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            result = {}
            for agent, val in raw.items():
                if isinstance(val, dict):
                    result[agent] = AgentStatus(
                        status=val.get("status", "failed"),
                        reason=val.get("reason"),
                    )
                elif isinstance(val, str):
                    result[agent] = AgentStatus(
                        status=val if val in PROGRESS_STATUSES else "failed",
                        reason=None if val in PROGRESS_STATUSES else val,
                    )
            return result
        except Exception:
            pass
    return {}


def _save_status_map(root: Path, mapping: dict[str, Any], topic: str) -> None:
    path = _status_path(root, topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = {}
    for agent, val in mapping.items():
        if isinstance(val, AgentStatus):
            serialized[agent] = val.to_dict()
        elif isinstance(val, dict):
            serialized[agent] = {"status": val.get("status"), "reason": val.get("reason")}
        elif hasattr(val, "status") and hasattr(val, "reason"):
            serialized[agent] = {"status": getattr(val, "status"), "reason": getattr(val, "reason")}
        else:
            serialized[agent] = {"status": str(val), "reason": None}
    path.write_text(json.dumps(serialized, indent=2), encoding="utf-8")


load_status_map = _load_status_map
save_status_map = _save_status_map
status_path = _status_path


def run_pass_zero(
    root: Path,
    models: list[tuple[str, str]],
    topic: str,
    *,
    settings: "config.Config",
    run_fn=None,
    runner=None,
    print_fn=None,
    progress_fn: Callable[[ProgressEvent], None] | None = None,
    status_map: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, str]:
    """Each model researches independently into its own temp file. A model
    that can't run or fails at runtime is recorded in pass-zero's status map
    and omitted from the returned successful-research map.

    `progress_fn`, when passed, receives a ProgressEvent for starting,
    running, and the terminal status of each selected model.
    `timeout_seconds` is the limit for every model in this pass. Omitted,
    each turn keeps chat's current limit.
    """
    print_fn = print_fn if print_fn is not None else print
    if timeout_seconds is None:
        timeout_seconds = _load_timeout(root, topic)
    elif timeout_seconds is not None:
        _save_timeout(root, timeout_seconds, topic)
    actual_agents: dict[str, str] = {}
    if status_map is None:
        status_map = {}
    total = len(models)
    for ordinal, (agent_key, label) in enumerate(models, start=1):
        exclude = frozenset(key for key, _ in models if key != agent_key)
        prompt = (
            f'Research "{topic}" independently. Write your findings to '
            f"{temp_path(root, agent_key)} as plain markdown. This is your "
            "own independent pass -- you haven't seen, and shouldn't need, "
            "any other model's perspective yet."
        )
        kwargs = {"run_fn": run_fn} if run_fn is not None else {}
        if runner is not None:
            kwargs["runner"] = runner
        watch = _begin_turn(
            print_fn=print_fn,
            progress_fn=progress_fn,
            agent=agent_key,
            label=label,
            phase=PHASE_PASS_ZERO,
            ordinal=ordinal,
            total=total,
        )
        try:
            record = chat.run_turn(
                root,
                agent=agent_key,
                prompt=prompt,
                settings=settings,
                commit_message=(
                    f'brainstorm: {agent_key} independent research on "{topic}"'
                ),
                commit_paths=owned_paths(root, topic, models),
                exclude=exclude,
                timeout_seconds=timeout_seconds,
                **kwargs,
            )
        except Exception as error:
            category = classify_failure(error=error)
            status = (
                "skipped"
                if isinstance(error, (agents.AgentMissing, chat.AgentUnavailable))
                else "failed"
            )
            reason = failure_reason(error=error)
            watch.emit(status, reason=reason)
            print_fn(f"{label} could not research this pass: {reason}")
            status_map[agent_key] = AgentStatus(status=status, reason=category)
            continue

        if not record["ok"]:
            reason = failure_reason(record=record)
            watch.emit("failed", reason=reason)
            print_fn(f"{label} could not research this pass: {reason}")
            status_map[agent_key] = AgentStatus(
                status="failed", reason=classify_failure(record=record)
            )
            continue

        _emit_outcome(watch, record)
        actual_key = record["agent"]
        actual_agents[agent_key] = actual_key
        status_map[agent_key] = AgentStatus(status="succeeded", reason=None)

    _save_actual_agents(root, actual_agents, topic)
    _save_status_map(root, status_map, topic)
    return actual_agents


def merge_pass_zero(
    root: Path,
    models: list[tuple[str, str]],
    topic: str,
    actual_agents: dict[str, str] | None = None,
) -> None:
    """Combines every model's non-empty temp file into the one shared file,
    under a `## <Label>` heading each, in `models`' own order. Deletes the
    temp files afterward. An empty or missing temp file is skipped, not an
    error (spec: "an empty/missing pass-0 temp file is skipped")."""
    model_labels = dict(MODEL_OPTIONS_BY_KEY)
    if actual_agents is None:
        if _actual_agents_path(root, topic).exists():
            actual_agents = _load_actual_agents(root, topic)
        else:
            actual_agents = None
    else:
        _save_actual_agents(root, actual_agents, topic)

    if actual_agents is not None and not actual_agents:
        for agent_key, _ in models:
            p = temp_path(root, agent_key)
            if p.exists():
                p.unlink(missing_ok=True)
        return

    sections = []
    used_paths = []
    for agent_key, label in models:
        if actual_agents is not None and agent_key not in actual_agents:
            continue
        path = temp_path(root, agent_key)
        if path.exists() and path.read_text(encoding="utf-8").strip():
            actual = actual_agents.get(agent_key, agent_key) if actual_agents else agent_key
            actual_label = model_labels.get(actual, label)
            sections.append(f"## {actual_label}\n\n{path.read_text(encoding='utf-8').strip()}\n")
            used_paths.append(path)
    target = shared_path(root, topic)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = f"# Brainstorm: {topic}\n\n" + "\n".join(sections)
    target.write_text(body, encoding="utf-8")
    for path in used_paths:
        path.unlink()
    for agent_key, _ in models:
        p = temp_path(root, agent_key)
        if p.exists():
            p.unlink(missing_ok=True)
    gitcheck.commit_paths(
        root, owned_paths(root, topic, models),
        f'brainstorm: merge independent research on "{topic}"',
    )


def _restore_shared(
    root: Path,
    shared: Path,
    pre_turn_content: str | None,
    topic: str,
    agent_key: str,
) -> None:
    if pre_turn_content is not None:
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text(pre_turn_content, encoding="utf-8")
        try:
            gitcheck.commit_paths(
                root,
                [shared],
                f'brainstorm: restore shared document after {agent_key} review failure on "{topic}"',
            )
        except Exception:
            pass
    elif shared.exists():
        shared.unlink()
        try:
            gitcheck.commit_paths(
                root,
                [shared],
                f'brainstorm: remove shared document after {agent_key} review failure on "{topic}"',
            )
        except Exception:
            pass


def _extract_research_sections(content: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_lines: list[str] = []
    for line in content.splitlines():
        if line.startswith("## "):
            if current_heading and current_heading != "Final Synthesis":
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)
    if current_heading and current_heading != "Final Synthesis":
        sections[current_heading] = "\n".join(current_lines).strip()
    return sections


def _has_usable_research(
    shared: Path,
    agent_key: str,
    label: str,
    actual_key: str | None = None,
) -> bool:
    if not shared.exists():
        return False
    try:
        content = shared.read_text(encoding="utf-8")
    except Exception:
        return False
    sections = _extract_research_sections(content)
    model_labels = dict(MODEL_OPTIONS_BY_KEY)
    possible_headings = {
        label,
        agent_key,
        model_labels.get(agent_key, label),
    }
    if actual_key:
        possible_headings.add(model_labels.get(actual_key, actual_key))
        possible_headings.add(actual_key)
    for h in possible_headings:
        if h in sections and bool(sections[h].strip()):
            return True
    return False


def run_review_pass(
    root: Path,
    models: list[tuple[str, str]],
    topic: str,
    pass_number: int,
    *,
    settings: "config.Config",
    run_fn=None,
    runner=None,
    print_fn=None,
    actual_agents: dict[str, str] | None = None,
    progress_fn: Callable[[ProgressEvent], None] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, str]:
    """Every selected model, once, revises only its own section. A model
    that can't run this pass is skipped (spec B7) -- its section simply
    keeps whatever it held from the last successful pass.

    `progress_fn`, when passed, receives one lifecycle per selected model.
    A model that cannot run is still skipped; its section is left alone.
    `timeout_seconds` is the limit for every model in this pass. Omitted,
    each turn keeps chat's current limit.
    """
    print_fn = print_fn if print_fn is not None else print
    if timeout_seconds is None:
        timeout_seconds = _load_timeout(root, topic)
    elif timeout_seconds is not None:
        _save_timeout(root, timeout_seconds, topic)
    shared = shared_path(root, topic)
    model_labels = dict(MODEL_OPTIONS_BY_KEY)
    if actual_agents is None:
        if _actual_agents_path(root, topic).exists():
            actual_map = _load_actual_agents(root, topic)
        else:
            actual_map = None
    else:
        actual_map = dict(actual_agents)

    if actual_map is not None:
        active_models = [m for m in models if m[0] in actual_map]
        results: dict[str, str] = dict(actual_map)
    else:
        active_models = list(models)
        actual_map = {agent_key: agent_key for agent_key, _ in models}
        results = dict(actual_map)

    total = len(active_models)
    for ordinal, (agent_key, label) in enumerate(active_models, start=1):
        exclude = frozenset(key for key, _ in models if key != agent_key)
        current_actual = actual_map.get(agent_key, agent_key)
        current_label = model_labels.get(current_actual, label)
        prompt = (
            f'Combined review pass {pass_number} of a brainstorm on '
            f'"{topic}". Read {shared} in full. Update your own section '
            f'("## {current_label}") in place based on what you now see from the '
            "others -- replace it with your revised thinking, rather than "
            "appending a new dated block; the file should only ever show "
            "your current view, not a history of past passes. Do not touch "
            "any other model's section."
        )
        kwargs = {"run_fn": run_fn} if run_fn is not None else {}
        if runner is not None:
            kwargs["runner"] = runner
        watch = _begin_turn(
            print_fn=print_fn,
            progress_fn=progress_fn,
            agent=agent_key,
            label=label,
            phase=PHASE_REVIEW,
            ordinal=ordinal,
            total=total,
            pass_number=pass_number,
        )
        pre_turn_content = shared.read_text(encoding="utf-8") if shared.exists() else None
        try:
            record = chat.run_turn(
                root,
                agent=agent_key,
                prompt=prompt,
                settings=settings,
                commit_message=(
                    f'brainstorm: {agent_key} review pass {pass_number} '
                    f'on "{topic}"'
                ),
                commit_paths=owned_paths(root, topic, models),
                exclude=exclude,
                timeout_seconds=timeout_seconds,
                **kwargs,
            )
        except (agents.AgentMissing, agents.AgentTimeout, chat.AgentUnavailable) as error:
            watch.emit("skipped", reason=str(error))
            print_fn(f"{label} could not review this pass: {error}")
            _restore_shared(root, shared, pre_turn_content, topic, agent_key)
            results[agent_key] = current_actual
            continue
        except Exception as error:
            watch.emit("failed", reason=failure_reason(error=error))
            print_fn(f"{label} could not review this pass: {failure_reason(error=error)}")
            _restore_shared(root, shared, pre_turn_content, topic, agent_key)
            results[agent_key] = current_actual
            continue
        _emit_outcome(watch, record)
        if not record["ok"]:
            print_fn(f"⚠ {label}'s review pass {pass_number} reported a failure.")
            _restore_shared(root, shared, pre_turn_content, topic, agent_key)
            results[agent_key] = current_actual
            continue
        actual_key = record["agent"]
        results[agent_key] = actual_key
        new_label = model_labels.get(actual_key, label)
        if new_label != current_label and shared.exists():
            content = shared.read_text(encoding="utf-8")
            new_content, count = re.subn(
                rf"^##\s+{re.escape(current_label)}\s*$",
                f"## {new_label}",
                content,
                count=1,
                flags=re.MULTILINE,
            )
            if count == 0 and f"## {current_label}" in content:
                new_content = content.replace(f"## {current_label}", f"## {new_label}", 1)
                count = 1
            if count > 0:
                shared.write_text(new_content, encoding="utf-8")
                gitcheck.commit_paths(
                    root, owned_paths(root, topic, models),
                    f'brainstorm: relabel section to {new_label} on "{topic}"',
                )
        actual_map[agent_key] = actual_key
    _save_actual_agents(root, results, topic)
    return results


def _strip_final_synthesis(shared: Path) -> None:
    if not shared.exists():
        return
    content = shared.read_text(encoding="utf-8")
    if "## Final Synthesis" not in content:
        return
    cleaned = re.sub(
        r"^##\s+Final Synthesis\s*?\n.*?(?=\n##\s|\Z)",
        "",
        content,
        flags=re.DOTALL | re.MULTILINE,
    ).strip()
    if cleaned:
        cleaned += "\n"
    shared.write_text(cleaned, encoding="utf-8")


def run_final_synthesis(
    root: Path,
    final_agent: str,
    models: list[tuple[str, str]],
    topic: str,
    *,
    settings: "config.Config",
    run_fn=None,
    runner=None,
    print_fn=None,
    actual_agents: dict[str, str] | None = None,
    progress_fn: Callable[[ProgressEvent], None] | None = None,
    timeout_seconds: int | None = None,
) -> dict:
    """One model writes the final synthesis. If the requested final agent fails
    at runtime, choose the first successful selected agent with usable research
    and announce the substitution. If no selected agent succeeds, stop with an
    actionable message and do not create an empty synthesis.

    `timeout_seconds` is the limit for the synthesis turn and for any
    substitute that runs after a failure. Omitted, the turn keeps chat's
    current limit.
    """
    print_fn = print_fn if print_fn is not None else print
    if timeout_seconds is None:
        timeout_seconds = _load_timeout(root, topic)
    elif timeout_seconds is not None:
        _save_timeout(root, timeout_seconds, topic)
    shared = shared_path(root, topic)
    kwargs = {"run_fn": run_fn} if run_fn is not None else {}
    if runner is not None:
        kwargs["runner"] = runner

    # Determine candidates with usable research
    if actual_agents is not None:
        candidates = [m for m in models if m[0] in actual_agents]
        actual_map = actual_agents
    else:
        disk_actual = (
            _load_actual_agents(root, topic)
            if _actual_agents_path(root, topic).exists()
            else {}
        )
        candidates = [
            m for m in models
            if not disk_actual or m[0] in disk_actual
        ]
        actual_map = disk_actual

    if shared.exists():
        sections = _extract_research_sections(shared.read_text(encoding="utf-8"))
        viable_candidates = [
            m
            for m in candidates
            if _has_usable_research(
                shared,
                m[0],
                m[1],
                actual_map.get(m[0]) if actual_map else None,
            )
        ] if sections else []
    else:
        viable_candidates = []

    if not viable_candidates:
        msg = (
            "No selected agent succeeded; stopping without synthesis. "
            "Check agent availability, authentication, or quotas and try again."
        )
        print_fn(msg)
        raise NothingToSynthesize(msg)

    viable_keys = {m[0] for m in viable_candidates}
    if final_agent not in viable_keys:
        chosen_agent, chosen_label = viable_candidates[0]
        failed_label = _model_label(models, final_agent)
        print_fn(
            f"{failed_label} failed at runtime; substituting "
            f"{chosen_label} for final synthesis."
        )
        target_agents = [m[0] for m in viable_candidates]
    else:
        target_agents = [final_agent] + [
            m[0] for m in viable_candidates if m[0] != final_agent
        ]

    prompt = (
        f'All review passes are complete for this brainstorm on "{topic}". '
        f"Read {shared} in full and write a new \"## Final Synthesis\" "
        "section (at the top, right after the title) combining the "
        "strongest ideas from every model's section into one clear, "
        "actionable recommendation."
    )

    last_error: BaseException | None = None
    last_record: dict | None = None

    for idx, current_agent in enumerate(target_agents):
        current_label = _model_label(models, current_agent)
        if idx > 0:
            prev_agent = target_agents[idx - 1]
            prev_label = _model_label(models, prev_agent)
            print_fn(
                f"{prev_label} failed at runtime; substituting "
                f"{current_label} for final synthesis."
            )

        watch = _begin_turn(
            print_fn=print_fn,
            progress_fn=progress_fn,
            agent=current_agent,
            label=current_label,
            phase=PHASE_SYNTHESIS,
            ordinal=1,
            total=1,
        )
        exclude = frozenset(key for key, _ in models if key != current_agent)
        try:
            record = chat.run_turn(
                root,
                agent=current_agent,
                prompt=prompt,
                settings=settings,
                commit_message=f'brainstorm: {current_agent} final synthesis on "{topic}"',
                commit_paths=owned_paths(root, topic, models),
                exclude=exclude,
                timeout_seconds=timeout_seconds,
                **kwargs,
            )
        except (agents.AgentMissing, chat.AgentUnavailable) as error:
            watch.emit("skipped", reason=str(error))
            _strip_final_synthesis(shared)
            last_error = error
            last_record = None
            continue
        except agents.AgentTimeout as error:
            watch.emit("failed", reason=FAILURE_TIMEOUT)
            _strip_final_synthesis(shared)
            last_error = error
            last_record = None
            continue
        except Exception as error:
            watch.emit("failed", reason=failure_reason(error=error))
            _strip_final_synthesis(shared)
            last_error = error
            last_record = None
            continue

        _emit_outcome(watch, record)
        if record["ok"]:
            return record

        _strip_final_synthesis(shared)
        last_record = record
        last_error = None
        continue

    # All attempted agents failed
    _strip_final_synthesis(shared)
    print_fn(
        "No selected agent succeeded for final synthesis. "
        "Check agent configurations, quotas, or credentials and try again."
    )
    if last_error is not None:
        raise last_error
    if last_record is not None:
        return last_record
    raise NothingToSynthesize("No selected agent succeeded for final synthesis.")


PLAN_GENERATION_PROMPT = (
    'Read {shared_path} in full, especially its "## Final Synthesis" '
    "section. Write a real plan.md at {draft_path} in whyline-relay's own "
    'checkbox format: one "- [ ] TASK-ID: short description" line per '
    "independently implementable and testable step, with indented detail "
    "lines below each explaining what to build and how to verify it. Base "
    "it on the Final Synthesis, translating its recommendation into "
    "concrete, ordered tasks."
    " If a decision the brainstorm does not settle blocks part of the plan, "
    'list it as a numbered item under an "## Open questions" heading at the '
    "top of the file, with the choices in the question itself, e.g. "
    '"1. Which broker? (a) Kite (b) Upstox"; still write every task you can.'
)


class NothingToSynthesize(RuntimeError):
    """The shared brainstorm doc has no real content to turn into a plan."""


def plan_draft_path(root: Path) -> Path:
    return config.relay_dir(root) / "brainstorm-plan-draft.md"


def owned_paths(root: Path, topic: str, models: Sequence[tuple[str, str]]) -> list[Path]:
    """Every file a brainstorm on `topic` writes. Its commits include these
    and nothing else: a brainstorm run while other work is uncommitted used to
    sweep that work into "brainstorm: ..." commits via `git add -A`, and an
    agent that strays outside its brief doesn't get that committed either."""
    return [
        *(temp_path(root, key) for key, _ in models),
        shared_path(root, topic),
        _actual_agents_path(root, topic),
        _status_path(root, topic),
        _timeout_path(root, topic),
        plan_draft_path(root),
    ]


def generate_plan_from_synthesis(
    root: Path,
    settings: "config.Config",
    final_agent: str,
    models: list[tuple[str, str]],
    topic: str,
    *,
    feedback: str | None = None,
    run_fn=None,
    runner=None,
    max_attempts: int = 2,
    timeout_seconds: int | None = None,
) -> Path:
    """Prompts final_agent to turn its own synthesis into a real plan.md,
    validating with plan.parse() and retrying once on a parse error. A draft
    that parses to no tasks is a PlanError too: prose with no checkboxes
    parses as an empty list. Raises NothingToSynthesize if the shared doc is
    empty or missing, or the last plan.PlanError if still invalid after
    max_attempts.
    agents.AgentMissing/AgentTimeout/chat.AgentUnavailable propagate
    immediately -- an availability failure is not a retry-worthy parse
    failure, and there is no "keep the previous content" fallback here
    since nothing has been generated yet.
    `timeout_seconds` applies to every draft attempt. Omitted, each attempt
    keeps chat's current limit."""
    if timeout_seconds is None:
        timeout_seconds = _load_timeout(root, topic)
    elif timeout_seconds is not None:
        _save_timeout(root, timeout_seconds, topic)
    shared = shared_path(root, topic)
    if not shared.exists() or not shared.read_text(encoding="utf-8").strip():
        raise NothingToSynthesize(
            f"{shared} is empty or missing -- nothing to turn into a plan"
        )
    draft = plan_draft_path(root)
    exclude = frozenset(key for key, _ in models if key != final_agent)
    kwargs = {"run_fn": run_fn} if run_fn is not None else {}
    if runner is not None:
        kwargs["runner"] = runner
    prompt = PLAN_GENERATION_PROMPT.format(shared_path=shared, draft_path=draft)
    if feedback:
        prompt += (
            f"\n\nA human reviewed a previous draft at {draft} and asked "
            f"for this change: {feedback}\n\nRewrite the whole file at "
            f"{draft} to address it, keeping the same checkbox format."
        )
    last_error: plan.PlanError | None = None
    for attempt in range(1, max_attempts + 1):
        chat.run_turn(
            root,
            agent=final_agent,
            prompt=prompt,
            settings=settings,
            commit_message=(
                f'brainstorm: {final_agent} drafts plan.md from synthesis '
                f'on "{topic}"'
            ),
            commit_paths=owned_paths(root, topic, models),
            exclude=exclude,
            timeout_seconds=timeout_seconds,
            **kwargs,
        )
        try:
            tasks = plan.parse(draft.read_text(encoding="utf-8"))
            if not tasks:
                raise plan.PlanError(f"{draft} has no checklist tasks")
            return draft
        except plan.PlanError as error:
            last_error = error
            if attempt < max_attempts:
                prompt = (
                    PLAN_GENERATION_PROMPT.format(
                        shared_path=shared, draft_path=draft
                    )
                    + f"\n\nYour previous attempt at {draft} did not parse "
                    f"as a valid plan: {error}\n\nRewrite the whole file "
                    f"at {draft}, fixing this."
                )
    raise last_error
