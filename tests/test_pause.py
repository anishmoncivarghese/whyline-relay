import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from whyline_relay import loop


def _clock() -> datetime:
    return datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc)


def test_pause_path_sits_beside_stop(tmp_path: Path):
    assert loop.pause_path(tmp_path) == tmp_path / ".whyline" / "relay" / "PAUSE"
    assert loop.pause_path(tmp_path).parent == loop.stop_path(tmp_path).parent


def test_request_pause_writes_the_time_and_mode(tmp_path: Path):
    loop.request_pause(tmp_path, clock=_clock)

    assert json.loads(loop.pause_path(tmp_path).read_text()) == {
        "requested_at": "2026-11-03T09:30:00+00:00",
        "now": False,
    }
    assert loop.pause_request(tmp_path) == loop.PauseRequest(
        requested_at="2026-11-03T09:30:00+00:00", now=False
    )
    # Written via a temp file, which must not be left behind.
    assert sorted(p.name for p in loop.pause_path(tmp_path).parent.iterdir()) == ["PAUSE"]


def test_request_pause_now(tmp_path: Path):
    loop.request_pause(tmp_path, now=True, clock=_clock)
    assert loop.pause_request(tmp_path).now is True


def test_no_request_without_the_file(tmp_path: Path):
    assert loop.pause_request(tmp_path) is None


def test_a_garbled_request_still_pauses(tmp_path: Path):
    path = loop.pause_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json")

    assert loop.pause_request(tmp_path) == loop.PauseRequest(requested_at="", now=False)


def test_cancel_pause_removes_the_request(tmp_path: Path):
    loop.request_pause(tmp_path, clock=_clock)

    assert loop.cancel_pause(tmp_path) is True
    assert loop.pause_request(tmp_path) is None
    assert loop.cancel_pause(tmp_path) is False


def test_the_stop_watcher_ignores_pause(tmp_path: Path):
    loop.request_pause(tmp_path, clock=_clock)
    cancel, done = threading.Event(), threading.Event()
    watcher = threading.Thread(target=loop._watch_for_stop, args=(tmp_path, cancel, done))
    watcher.start()
    try:
        assert not cancel.wait(loop._STOP_POLL_SECONDS * 5)
        assert not loop.stop_requested(tmp_path)
    finally:
        done.set()
        watcher.join()
    assert not cancel.is_set()
