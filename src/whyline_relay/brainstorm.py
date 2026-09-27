"""Multi-model brainstorming: independent research, combined review passes,
then one model's final synthesis -- all built on ordinary chat turns."""

from __future__ import annotations

import re
from pathlib import Path

from whyline_relay import agents, chat, config, gitcheck

MODEL_OPTIONS = (
    ("1", "claude", "Claude"),
    ("2", "codex", "Codex"),
    ("3", "agy", "Antigravity"),
    ("4", "grok", "Grok"),
)


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


def ask_brainstorm_setup(
    root: Path,
    settings: "config.Config",
    *,
    input_fn=None,
    print_fn=None,
) -> dict | None:
    """Asks topic/models/passes/final-model, validating and reprompting.
    Returns {"topic", "models", "passes", "final_agent"}, or None if the
    user declined to proceed after an availability warning.
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

    return {
        "topic": topic,
        "models": models,
        "passes": passes,
        "final_agent": final_agent,
    }


def temp_path(root: Path, agent: str) -> Path:
    return config.relay_dir(root) / "brainstorm-tmp" / f"{agent}.md"


def shared_path(root: Path, topic: str) -> Path:
    return root / "docs" / "brainstorm" / f"{slugify(topic)}.md"


def run_pass_zero(
    root: Path,
    models: list[tuple[str, str]],
    topic: str,
    *,
    settings: "config.Config",
    run_fn=None,
    runner=None,
    print_fn=None,
) -> None:
    """Each model researches independently into its own temp file. A model
    that can't run is skipped (spec B7) -- it simply leaves no temp file,
    which merge_pass_zero already treats as absent, not an error."""
    print_fn = print_fn if print_fn is not None else print
    for agent_key, label in models:
        prompt = (
            f'Research "{topic}" independently. Write your findings to '
            f"{temp_path(root, agent_key)} as plain markdown. This is your "
            "own independent pass -- you haven't seen, and shouldn't need, "
            "any other model's perspective yet."
        )
        kwargs = {"run_fn": run_fn} if run_fn is not None else {}
        if runner is not None:
            kwargs["runner"] = runner
        try:
            chat.run_turn(
                root,
                agent=agent_key,
                prompt=prompt,
                settings=settings,
                commit_message=(
                    f'brainstorm: {agent_key} independent research on "{topic}"'
                ),
                **kwargs,
            )
        except (agents.AgentMissing, agents.AgentTimeout, chat.AgentUnavailable) as error:
            print_fn(f"{label} could not research this pass: {error}")


def merge_pass_zero(root: Path, models: list[tuple[str, str]], topic: str) -> None:
    """Combines every model's non-empty temp file into the one shared file,
    under a `## <Label>` heading each, in `models`' own order. Deletes the
    temp files afterward. An empty or missing temp file is skipped, not an
    error (spec: "an empty/missing pass-0 temp file is skipped")."""
    sections = []
    used_paths = []
    for agent_key, label in models:
        path = temp_path(root, agent_key)
        if path.exists() and path.read_text(encoding="utf-8").strip():
            sections.append(f"## {label}\n\n{path.read_text(encoding='utf-8').strip()}\n")
            used_paths.append(path)
    target = shared_path(root, topic)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = f"# Brainstorm: {topic}\n\n" + "\n".join(sections)
    target.write_text(body, encoding="utf-8")
    for path in used_paths:
        path.unlink()
    gitcheck.commit_all(root, f'brainstorm: merge independent research on "{topic}"')


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
) -> None:
    """Every selected model, once, revises only its own section. A model
    that can't run this pass is skipped (spec B7) -- its section simply
    keeps whatever it held from the last successful pass."""
    print_fn = print_fn if print_fn is not None else print
    shared = shared_path(root, topic)
    for agent_key, label in models:
        prompt = (
            f'Combined review pass {pass_number} of a brainstorm on '
            f'"{topic}". Read {shared} in full. Update your own section '
            f'("## {label}") in place based on what you now see from the '
            "others -- replace it with your revised thinking, rather than "
            "appending a new dated block; the file should only ever show "
            "your current view, not a history of past passes. Do not touch "
            "any other model's section."
        )
        kwargs = {"run_fn": run_fn} if run_fn is not None else {}
        if runner is not None:
            kwargs["runner"] = runner
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
                **kwargs,
            )
        except (agents.AgentMissing, agents.AgentTimeout, chat.AgentUnavailable) as error:
            print_fn(f"{label} could not review this pass: {error}")
            continue
        if not record["ok"]:
            print_fn(f"⚠ {label}'s review pass {pass_number} reported a failure.")


def run_final_synthesis(
    root: Path,
    final_agent: str,
    models: list[tuple[str, str]],
    topic: str,
    *,
    settings: "config.Config",
    run_fn=None,
    runner=None,
) -> dict:
    shared = shared_path(root, topic)
    prompt = (
        f'All review passes are complete for this brainstorm on "{topic}". '
        f"Read {shared} in full and write a new \"## Final Synthesis\" "
        "section (at the top, right after the title) combining the "
        "strongest ideas from every model's section into one clear, "
        "actionable recommendation."
    )
    kwargs = {"run_fn": run_fn} if run_fn is not None else {}
    if runner is not None:
        kwargs["runner"] = runner
    return chat.run_turn(
        root,
        agent=final_agent,
        prompt=prompt,
        settings=settings,
        commit_message=f'brainstorm: {final_agent} final synthesis on "{topic}"',
        **kwargs,
    )
