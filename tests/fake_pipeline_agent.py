"""A fake agent for configured-pipeline tests: argv[1] is "to_actor:status",
optionally "#q1|q2" for a handoff's questions, or "commit:to_actor:status" to also make a git commit before handing off."""

import json
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    spec = sys.argv[1]
    spec, _, asked = spec.partition("#")
    questions = [q for q in asked.split("|") if q]
    root = Path(sys.argv[2])
    prompt = sys.argv[-1]
    match = re.search(r"^## Task (\S+)", prompt, re.M)
    task_id = match.group(1) if match else "T-1"
    parts = spec.split(":")
    make_commit = parts[0] == "commit"
    make_write = parts[0] in ("commit", "write")
    to_actor, status = parts[-2], parts[-1]
    if make_write:
        (root / "feature.txt").write_text(f"{task_id}\n")
        (root / f"feature-{task_id}.txt").write_text(f"{task_id}\n")
    if make_commit:
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"feat: x ({task_id})"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    target = root / ".whyline" / "active-handoff.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(target.read_text()) if target.exists() else {}
    previous_counter = previous.get("counter")
    if previous_counter is None:
        event_match = re.fullmatch(r"event(\d+)", previous.get("id", ""))
        previous_counter = event_match.group(1) if event_match else 0
    counter = int(previous_counter) + 1
    target.write_text(
        json.dumps(
            {
                "id": f"event{counter}",
                "counter": counter,
                "task": task_id,
                "to_actor": to_actor,
                "status": status,
                "summary": f"fake {spec}",
                "from_actor": "",
                "questions": questions,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
