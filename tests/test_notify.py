from whyline_relay import notify


def test_macos_uses_osascript():
    argv = notify.command("Relay", "Paused on WL-1", "darwin")
    assert argv[0] == "osascript"
    assert any("Paused on WL-1" in part for part in argv)


def test_linux_uses_notify_send():
    argv = notify.command("Relay", "Paused on WL-1", "linux")
    assert argv[0] == "notify-send"


def test_unknown_platform_gets_no_command():
    assert notify.command("Relay", "x", "win32") is None


def test_send_never_raises_when_the_notifier_is_missing():
    def explode(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    notify.send("Relay", "x", runner=explode, platform="linux")


def test_quotes_in_the_message_do_not_break_the_applescript():
    argv = notify.command("Relay", 'he said "stop"', "darwin")
    assert '"stop"' not in argv[-1] or "\\" in argv[-1]
