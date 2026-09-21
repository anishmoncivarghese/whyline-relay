import subprocess
import sys
from pathlib import Path

from whyline_relay import cli, plan, planhelp


def test_plan_format_prints_rules_and_prompt(capsys):
    assert cli.main(["plan-format"]) == cli.EXIT_OK

    assert capsys.readouterr().out == (
        f"{planhelp.RULES}\n\n"
        "Prompt to give an AI that drafts your plan:\n"
        f"{planhelp.PROMPT}\n"
    )


def test_plan_format_prompt_prints_only_prompt(capsys):
    assert cli.main(["plan-format", "--prompt"]) == cli.EXIT_OK

    assert capsys.readouterr().out == f"{planhelp.PROMPT}\n"


def test_plan_help_covers_the_essential_rules():
    text = f"{planhelp.RULES}\n{planhelp.PROMPT}"

    assert "- [ ]" in text
    assert "ID:" in text
    assert "fresh agent" in text


def test_prompt_example_is_a_valid_two_task_plan():
    example = planhelp.PROMPT.split("```markdown\n", 1)[1].split("```", 1)[0]

    tasks = plan.parse(example)

    assert [(task.task_id, task.checked) for task in tasks] == [
        ("APP-1", False),
        ("APP-2", False),
    ]
    assert tasks[1].text.startswith("APP-2: Expose configuration in the CLI")


def test_prompt_is_under_200_words():
    assert len(planhelp.PROMPT.split()) < 200


def test_plan_format_works_outside_a_git_repository(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "whyline_relay.cli", "plan-format"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert planhelp.RULES in result.stdout
    assert planhelp.PROMPT in result.stdout
    assert result.stderr == ""
