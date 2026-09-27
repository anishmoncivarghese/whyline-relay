"""Multi-model brainstorming: independent research, combined review passes,
then one model's final synthesis -- all built on ordinary chat turns."""

from __future__ import annotations

import re
from pathlib import Path

from whyline_relay import chat, config

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
