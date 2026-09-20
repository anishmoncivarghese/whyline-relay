"""A fake agent. Prints, optionally writes a handoff, optionally hangs."""

import json
import sys
import time
from pathlib import Path


def main() -> int:
    mode = sys.argv[1]
    root = Path(sys.argv[2])
    prompt = sys.argv[-1]
    print(f"fake-agent {mode} received {len(prompt)} chars of prompt")
    if mode == "hang":
        time.sleep(600)
    if mode == "silent":
        return 0
    if mode == "fail":
        print("fake-agent failing on purpose", file=sys.stderr)
        return 1
    if mode == "ratelimited":
        print("You have exceeded your usage limit. Try again later.")
        return 1
    to_actor, status = {
        "review": ("claude", "ready-for-review"),
        "approve": ("claude", "approved"),
        "changes": ("codex", "changes-requested"),
        "blocked": ("claude", "blocked"),
        "weird": ("claude", "banana"),
    }[mode]
    target = root / ".whyline"
    target.mkdir(parents=True, exist_ok=True)
    existing = target / "active-handoff.json"
    previous = json.loads(existing.read_text()) if existing.exists() else {}
    counter = int(previous.get("counter", 0)) + 1
    existing.write_text(
        json.dumps(
            {
                "v": 1,
                "id": f"event{counter}",
                "counter": counter,
                "type": "Handoff",
                "task": sys.argv[3] if len(sys.argv) > 4 else "WL-1",
                "from_actor": "fake",
                "to_actor": to_actor,
                "status": status,
                "summary": f"fake {mode}",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
