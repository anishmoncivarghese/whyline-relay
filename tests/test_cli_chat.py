import os
from pathlib import Path

from whyline_relay import cli


def test_chat_subcommand_is_registered():
    parser = cli.build_parser()
    args = parser.parse_args(["chat"])
    assert args.command == "chat"


def test_cmd_chat_calls_chat_repl(tmp_path: Path, monkeypatch):
    from whyline_relay import chat

    calls = []
    monkeypatch.setattr(chat, "repl", lambda root, **kwargs: calls.append(root))
    previous = os.getcwd()
    os.chdir(tmp_path)
    try:
        args = cli.build_parser().parse_args(["chat"])
        code = cli.cmd_chat(args)
    finally:
        os.chdir(previous)
    assert code == cli.EXIT_OK
    assert calls == [tmp_path.resolve()]
