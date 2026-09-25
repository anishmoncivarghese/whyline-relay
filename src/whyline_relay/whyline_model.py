"""Reading whyline's own per-repo model choice (.whyline/model.json), read-only.

whyline-relay never writes to this file -- `whyline model` is its only writer
-- and never imports whyline as a library, matching how whylinecmd.py already
only ever shells out to the `whyline` binary. This reads a plain JSON file
whyline owns the shape of directly, since there is nothing to invoke a command
for; a missing or corrupt file reads as absent, never a crash, matching
state.py's own established convention for every other file this project reads.
"""

from __future__ import annotations

import json
from pathlib import Path


def read(root: Path) -> dict:
    try:
        data = json.loads((root / ".whyline" / "model.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
