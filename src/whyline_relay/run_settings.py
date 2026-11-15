"""Live timeout, round-cap and backup-chain changes for a running relay.

The loop never watches this module. It re-reads `.whyline/relay/run-settings.json`
where it uses the values, the same way it re-reads model-overrides.json. None on
a field means that field is not changed this run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from whyline_relay import config, roles, running, state


class RunSettingsError(ValueError):
    """A settings change cannot be honored as given."""


class Unreadable(ValueError):
    """run-settings.json exists but is not a usable record."""


_FIELDS = ("timeout_minutes", "max_rounds", "backup_chain")
_RESET_NAMES = {
    "timeout": "timeout_minutes",
    "rounds": "max_rounds",
    "backup": "backup_chain",
}
_UNREADABLE_PROBLEM = (
    "run-settings.json can't be read; using the configured values"
)


@dataclass(frozen=True)
class RunSettings:
    timeout_minutes: int | None = None
    max_rounds: int | None = None
    backup_chain: tuple[str, ...] | None = None
    since: str = ""
    # Field names written with keep=True. A single bool would forget a kept
    # timeout when a later rounds change omits --keep.
    keep: tuple[str, ...] = ()


@dataclass(frozen=True)
class Effective:
    timeout_minutes: int
    max_rounds: int
    backup_chain: tuple[str, ...]
    changed: dict[str, tuple[object, object]]
    problem: str | None = None


def path(root: Path) -> Path:
    return config.relay_dir(root) / "run-settings.json"


def read(root: Path) -> RunSettings:
    """The stored changes. A missing file is no changes; a file that cannot
    be read raises Unreadable, so the caller can say so rather than silently
    applying a value nobody asked for."""
    target = path(root)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RunSettings()
    except (OSError, UnicodeDecodeError) as error:
        raise Unreadable(str(error)) from error
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise Unreadable(str(error)) from error
    if not isinstance(raw, dict):
        raise Unreadable("not a JSON object")
    timeout_minutes = _optional_int(raw, "timeout_minutes")
    max_rounds = _optional_int(raw, "max_rounds")
    backup_chain = _optional_chain(raw)
    since = raw.get("since", "")
    if not isinstance(since, str):
        raise Unreadable("since is not a string")
    present = tuple(
        name
        for name, value in (
            ("timeout_minutes", timeout_minutes),
            ("max_rounds", max_rounds),
            ("backup_chain", backup_chain),
        )
        if value is not None
    )
    keep = _parse_keep(raw.get("keep"), present)
    return RunSettings(
        timeout_minutes=timeout_minutes,
        max_rounds=max_rounds,
        backup_chain=backup_chain,
        since=since,
        keep=keep,
    )


def effective(root: Path, settings: config.Config) -> Effective:
    """Configured values overlaid with any readable changes. Never raises."""
    try:
        stored = read(root)
    except Unreadable:
        return Effective(
            timeout_minutes=settings.timeout_minutes,
            max_rounds=settings.max_rounds,
            backup_chain=tuple(settings.backup_chain),
            changed={},
            problem=_UNREADABLE_PROBLEM,
        )
    changed: dict[str, tuple[object, object]] = {}
    timeout_minutes = settings.timeout_minutes
    if (
        stored.timeout_minutes is not None
        and stored.timeout_minutes != settings.timeout_minutes
    ):
        timeout_minutes = stored.timeout_minutes
        changed["timeout_minutes"] = (
            settings.timeout_minutes,
            stored.timeout_minutes,
        )
    max_rounds = settings.max_rounds
    if stored.max_rounds is not None and stored.max_rounds != settings.max_rounds:
        max_rounds = stored.max_rounds
        changed["max_rounds"] = (settings.max_rounds, stored.max_rounds)
    backup_chain = tuple(settings.backup_chain)
    if (
        stored.backup_chain is not None
        and stored.backup_chain != tuple(settings.backup_chain)
    ):
        backup_chain = stored.backup_chain
        changed["backup_chain"] = (tuple(settings.backup_chain), stored.backup_chain)
    return Effective(
        timeout_minutes=timeout_minutes,
        max_rounds=max_rounds,
        backup_chain=backup_chain,
        changed=changed,
    )


def set_timeout(
    root: Path, settings: config.Config, minutes: int, *, keep: bool = False
) -> str:
    """Apply `minutes` from the next turn. 0 means no limit."""
    _require_int(minutes, "timeout")
    if minutes < 0 or minutes > 1440:
        raise RunSettingsError(
            "timeout must be 0 (no limit) or 1 to 1440 minutes"
        )
    stored = _readable(root)
    stored = _put(
        stored,
        "timeout_minutes",
        None if minutes == settings.timeout_minutes else minutes,
        keep=keep,
    )
    _write(root, stored)
    if keep and minutes != settings.timeout_minutes:
        _keep_int(root, "timeout_minutes", minutes)
    return f"Timeout {minutes}m from the next turn."


def set_rounds(
    root: Path,
    settings: config.Config,
    rounds: int,
    *,
    keep: bool = False,
    current_round: int | None = None,
) -> str:
    """Apply `rounds` from the next round check."""
    _require_int(rounds, "round cap")
    if rounds < 1 or rounds > 99:
        raise RunSettingsError("round cap must be 1 to 99")
    if current_round is not None and rounds < current_round:
        marker = running.read(root)
        task = marker.task if marker is not None else "this task"
        raise RunSettingsError(
            f"{task} is already on round {current_round}; "
            "the cap can't go below that"
        )
    stored = _readable(root)
    stored = _put(
        stored,
        "max_rounds",
        None if rounds == settings.max_rounds else rounds,
        keep=keep,
    )
    _write(root, stored)
    if keep and rounds != settings.max_rounds:
        _keep_int(root, "max_rounds", rounds)
    return f"Round cap {rounds} from the next check."


def set_backup(
    root: Path,
    settings: config.Config,
    chain,
    *,
    keep: bool = False,
) -> str:
    """Apply `chain` from the next time a backup is needed. Empty means none."""
    names = _validated_chain(settings, chain)
    stored = _readable(root)
    stored = _put(
        stored,
        "backup_chain",
        None if names == tuple(settings.backup_chain) else names,
        keep=keep,
    )
    _write(root, stored)
    if keep and names != tuple(settings.backup_chain):
        _keep_backup(root, names)
    return _backup_message(names)


def reset(root: Path, what: str | None = None) -> str:
    """Remove one stored change, or every stored change when `what` is None."""
    stored = _readable(root)
    if what is None:
        if not path(root).exists():
            return "Nothing to reset."
        path(root).unlink(missing_ok=True)
        return "Timeout, round cap and backups reset."
    field = _RESET_NAMES.get(what)
    if field is None:
        raise RunSettingsError(
            f"reset {what!r} is not timeout, rounds, or backup"
        )
    if getattr(stored, field) is None:
        return "Nothing to reset."
    stored = _put(stored, field, None, keep=False)
    _write(root, stored)
    return {
        "timeout": "Timeout reset.",
        "rounds": "Round cap reset.",
        "backup": "Backups reset.",
    }[what]


def clear(root: Path, *, completed: bool) -> None:
    """Drop the file when a run ends. Kept values are already in config.toml."""
    if completed:
        path(root).unlink(missing_ok=True)


def _optional_int(raw: dict, key: str) -> int | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise Unreadable(f"{key} is not an integer")
    return value


def _optional_chain(raw: dict) -> tuple[str, ...] | None:
    if "backup_chain" not in raw or raw["backup_chain"] is None:
        return None
    value = raw["backup_chain"]
    if not isinstance(value, list) or not all(
        isinstance(name, str) and name for name in value
    ):
        raise Unreadable("backup_chain is not a list of names")
    return tuple(value)


def _parse_keep(raw: object, present: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, bool):
        return present if raw else ()
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return tuple(item for item in raw if item in _FIELDS)
    if isinstance(raw, dict):
        return tuple(
            key for key, flag in raw.items() if key in _FIELDS and flag is True
        )
    raise Unreadable("keep is not usable")


def _readable(root: Path) -> RunSettings:
    try:
        return read(root)
    except Unreadable:
        return RunSettings()


def _put(
    stored: RunSettings, field: str, value, *, keep: bool
) -> RunSettings:
    kept = tuple(name for name in stored.keep if name != field)
    if keep and value is not None:
        kept = (*kept, field)
    return replace(
        stored,
        **{
            field: value,
            "since": datetime.now().astimezone().isoformat(),
            "keep": kept,
        },
    )


def _write(root: Path, record: RunSettings) -> None:
    target = path(root)
    if (
        record.timeout_minutes is None
        and record.max_rounds is None
        and record.backup_chain is None
    ):
        target.unlink(missing_ok=True)
        return
    payload = {
        "timeout_minutes": record.timeout_minutes,
        "max_rounds": record.max_rounds,
        "backup_chain": (
            list(record.backup_chain) if record.backup_chain is not None else None
        ),
        "since": record.since,
        "keep": list(record.keep),
    }
    state._write_atomic(target, json.dumps(payload, indent=2) + "\n")


def _require_int(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RunSettingsError(f"{label} must be an integer")


def _validated_chain(settings: config.Config, chain) -> tuple[str, ...]:
    try:
        names = tuple(chain)
    except TypeError as error:
        raise RunSettingsError("backup chain must be a list of agent names") from error
    seen: set[str] = set()
    holder = _agent_of_all_roles(settings)
    for name in names:
        if not isinstance(name, str) or not name:
            raise RunSettingsError("backup chain entries must be non-empty names")
        if name not in settings.agents:
            raise RunSettingsError(f"{name} is not set up for this relay")
        if name in seen:
            raise RunSettingsError(f"{name} is listed twice")
        if holder is not None and name == holder:
            raise RunSettingsError(
                f"{name} is the configured agent of all three roles"
            )
        seen.add(name)
    return names


def _agent_of_all_roles(settings: config.Config) -> str | None:
    assigned = roles.current_roles(settings)
    if len(assigned) < 3:
        return None
    unique = set(assigned.values())
    if len(unique) == 1:
        return next(iter(unique))
    return None


def _backup_message(chain: tuple[str, ...]) -> str:
    if not chain:
        names = "none"
    else:
        names = ", then ".join(chain)
    return f"Backups: {names}, from the next time one is needed."


def _keep_int(root: Path, key: str, value: int) -> None:
    target = config.config_path(root)
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_upsert_top_level_int(text, key, value), encoding="utf-8")


def _keep_backup(root: Path, chain: tuple[str, ...]) -> None:
    target = config.config_path(root)
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_upsert_backup(text, list(chain)), encoding="utf-8")


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _upsert_top_level_int(text: str, key: str, value: int) -> str:
    """Set `key = <integer>` above the first table. Every other line stays."""
    rendered = str(int(value))
    lines = text.splitlines(keepends=True)
    top_end = next(
        (index for index, line in enumerate(lines) if line.strip().startswith("[")),
        len(lines),
    )
    key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*).*$")
    for index in range(top_end):
        ending = _line_ending(lines[index])
        body = lines[index][: len(lines[index]) - len(ending)] if ending else lines[index]
        match = key_re.match(body)
        if match:
            newline = ending if ending else "\n"
            lines[index] = f"{match.group(1)}{rendered}{newline}"
            return "".join(lines)
    entry = f"{key} = {rendered}\n"
    keys = [
        index
        for index in range(top_end)
        if lines[index].strip() and not lines[index].lstrip().startswith("#")
    ]
    insert_at = keys[-1] + 1 if keys else 0
    if insert_at and not lines[insert_at - 1].endswith(("\n", "\r")):
        lines[insert_at - 1] += "\n"
    if insert_at == 0 and top_end < len(lines):
        entry += "\n"
    lines.insert(insert_at, entry)
    return "".join(lines)


def _upsert_backup(text: str, chain: list[str]) -> str:
    """Set or remove `chain = [...]` under [backup]. Every other line stays.

    Same shape as roles._upsert_agent_model: find the table, change only that
    key's line, create the table when it is missing. An empty chain removes
    the chain line and leaves comments and unknown keys in place.
    """
    quoted = ", ".join(f'"{name}"' for name in chain)
    rendered = f"[{quoted}]"
    lines = text.splitlines(keepends=True)
    table_start = None
    table_end = len(lines)
    for index, line in enumerate(lines):
        if table_start is None and line.strip() == "[backup]":
            table_start = index
            continue
        if (
            table_start is not None
            and index > table_start
            and re.match(r"^\[", line.strip())
        ):
            table_end = index
            break

    key_re = re.compile(r"^(\s*chain\s*=\s*).*$")
    if table_start is not None:
        for index in range(table_start + 1, table_end):
            ending = _line_ending(lines[index])
            body = (
                lines[index][: len(lines[index]) - len(ending)]
                if ending
                else lines[index]
            )
            match = key_re.match(body)
            if match:
                if not chain:
                    del lines[index]
                    return "".join(lines)
                newline = ending if ending else "\n"
                lines[index] = f"{match.group(1)}{rendered}{newline}"
                return "".join(lines)
        if not chain:
            return "".join(lines)
        if table_end and not lines[table_end - 1].endswith(("\n", "\r")):
            lines[table_end - 1] += "\n"
        lines.insert(table_end, f"chain = {rendered}\n")
        return "".join(lines)

    if not chain:
        return text
    suffix = "" if (not text or text.endswith("\n")) else "\n"
    return text + suffix + f"\n[backup]\nchain = {rendered}\n"
