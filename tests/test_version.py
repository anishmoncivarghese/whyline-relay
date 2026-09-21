from importlib import metadata

import pytest

import whyline_relay
from whyline_relay import cli


def test_version_prints_package_metadata_and_exits_successfully(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--version"])

    assert stopped.value.code == 0
    assert capsys.readouterr().out == f"whyline-relay {metadata.version('whyline-relay')}\n"


def test_version_falls_back_when_package_metadata_is_missing(monkeypatch, capsys):
    def missing_version(distribution_name: str) -> str:
        raise metadata.PackageNotFoundError(distribution_name)

    monkeypatch.setattr(cli.metadata, "version", missing_version)

    with pytest.raises(SystemExit) as stopped:
        cli.main(["--version"])

    assert stopped.value.code == 0
    assert capsys.readouterr().out == "whyline-relay unknown\n"


def test_version_option_has_help():
    action = next(
        action
        for action in cli.build_parser()._actions
        if "--version" in action.option_strings
    )

    assert isinstance(action.help, str)
    assert action.help.strip()


def test_module_version_matches_package_metadata():
    assert whyline_relay.__version__ == metadata.version("whyline-relay")
