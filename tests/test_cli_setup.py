import os
from pathlib import Path

from whyline_relay import cli


def test_setup_subcommand_is_registered():
    parser = cli.build_parser()
    args = parser.parse_args(["setup"])
    assert args.command == "setup"


def test_cmd_setup_calls_setup_run(tmp_path: Path, monkeypatch):
    from whyline_relay import setup

    calls = []
    monkeypatch.setattr(setup, "run", lambda root, **kwargs: calls.append(root) or 0)

    previous = os.getcwd()
    os.chdir(tmp_path)
    try:
        args = cli.build_parser().parse_args(["setup"])
        code = cli.cmd_setup(args)
    finally:
        os.chdir(previous)

    assert code == cli.EXIT_OK
    assert calls == [tmp_path.resolve()]
