"""A fake agent that hands off under exactly the names it is told.
argv: root from_actor to_actor status commit(yes|no) <prompt>   (the relay appends the prompt last)
"""

import json
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root, from_actor, to_actor, status, commit = sys.argv[1:6]
    prompt = sys.argv[-1]
    task = re.search(r"^## Task (\S+)", prompt, re.M).group(1)
    if commit == "yes":
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"feat: work ({task})"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    target = Path(root) / ".whyline" / "active-handoff.json"
    target.parent.mkdir(exist_ok=True)
    previous = json.loads(target.read_text()) if target.exists() else {}
    counter = int(previous.get("counter", 0)) + 1
    target.write_text(
        json.dumps(
            {
                "v": 1,
                "id": f"role{counter}",
                "counter": counter,
                "type": "Handoff",
                "task": task,
                "from_actor": from_actor,
                "to_actor": to_actor,
                "status": status,
                "summary": "fake",
            }
        )
    )
    print(f"fake-role-agent {from_actor} -> {to_actor} {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
