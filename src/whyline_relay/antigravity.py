"""Antigravity's trust setting. `agy` refuses even to read files outside
the folders listed in trustedWorkspaces, in one settings file for the whole
machine, so a repository has to be added there before it can take part.
Only trust() writes that file, and only when a person said yes."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from whyline_relay import config

# The README's verified set; narrower patterns did not work.
ALLOW = ("read_file(*)", "write_file(*)", "edit_file(*)", "command(*)")


class SettingsUnreadable(RuntimeError):
    """The settings file exists but isn't a JSON object of the expected shape."""


def settings_path() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def _load() -> dict:
    path = settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SettingsUnreadable(f"{path} is not valid JSON ({error}); fix it by hand") from error
    workspaces = data.get("trustedWorkspaces", []) if isinstance(data, dict) else None
    permissions = data.get("permissions", {}) if isinstance(data, dict) else None
    allow = permissions.get("allow", []) if isinstance(permissions, dict) else None
    if not all(isinstance(value, list) for value in (workspaces, allow)):
        raise SettingsUnreadable(
            f"{path} has an unexpected shape (trustedWorkspaces and permissions.allow "
            "must be lists); fix it by hand"
        )
    return data


def is_trusted(root: Path) -> bool:
    try:
        data = _load()
    except SettingsUnreadable:
        return False
    return str(Path(root).resolve()) in data.get("trustedWorkspaces", [])


def trust(root: Path) -> Path:
    data = _load()
    workspaces = data.setdefault("trustedWorkspaces", [])
    repo = str(Path(root).resolve())
    if repo not in workspaces:
        workspaces.append(repo)
    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    allow.extend(rule for rule in ALLOW if rule not in allow)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(data, out, indent=2)
        out.write("\n")
    os.replace(temp, path)
    return path


def declined_path(root: Path) -> Path:
    return config.relay_dir(root) / "antigravity-declined"


def is_declined(root: Path) -> bool:
    return declined_path(root).exists()


def decline(root: Path) -> None:
    path = declined_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("The user chose not to trust this repository for Antigravity.\n")


def forget_decline(root: Path) -> None:
    declined_path(root).unlink(missing_ok=True)
