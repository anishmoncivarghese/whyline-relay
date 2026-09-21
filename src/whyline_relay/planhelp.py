"""Built-in guidance for writing relay plans."""

from whyline_relay import invocation

_DEFAULT_PROG = invocation.prog()

RULES = f"""Whyline relay plan format

- Write each task as `- [ ] ID: title`, followed by indented detail lines.
- Always write `ID:`. The id is the text before the first colon, or the first word when there is no colon. Ids must be unique.
- Headings, prose, and blank lines are ignored. Checked (`- [x]`) tasks are skipped.
- Tasks run in file order. Each task builds on the commits made by earlier tasks.
- A fresh agent with no memory of earlier tasks receives each task. Make every task stand alone: say what to build and where, cover edge cases, name the tests to add, and say \"change nothing else\".
- Keep each task to about one reviewable change.
- For phases that need a review point, use one plan file per phase and start each phase with `{_DEFAULT_PROG} start --plan PATH`."""

PROMPT = """Write a whyline-relay plan in exactly this Markdown format. Each task must start `- [ ] ID: title`, use a unique `ID:`, and put all details on indented lines. Headings, prose, and blank lines may organize the file. Tasks run in file order and build on earlier commits, but a fresh agent with no memory of earlier tasks receives each task. Make every task stand alone: specify what to build and where, edge cases, tests to add, and `Change nothing else.` Keep each task to about one reviewable change. Use one plan file per phase when a human review point is needed.

Example:

```markdown
- [ ] APP-1: Add configuration loading
  Add `load_config()` in `src/app/config.py` with a clear error for invalid TOML.
  Add unit tests for valid and invalid files. Change nothing else.
- [ ] APP-2: Expose configuration in the CLI
  Add `--config PATH` in `src/app/cli.py`, using the loader from APP-1.
  Test the default and explicit paths. Change nothing else.
```"""


def rules() -> str:
    """Return plan rules using the current invocation name."""
    return RULES.replace(
        f"`{_DEFAULT_PROG} start --plan PATH`",
        f"`{invocation.command('start')} --plan PATH`",
    )


def prompt() -> str:
    """Return the paste-ready prompt for the current invocation."""
    return PROMPT
