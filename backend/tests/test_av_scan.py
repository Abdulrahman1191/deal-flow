"""Inbound deck antivirus scan (app/services/av_scan.py). No clamd: the socket is faked."""
import struct
from pathlib import Path

import pytest

from app.config import settings
from app.services import av_scan
from app.services.av_scan import DeckQuarantined, ScanResult, check_deck, parse_reply, scan_file


class FakeSock:
    def __init__(self, reply: bytes, fail_send: bool = False):
        self.reply, self.fail_send, self.sent = reply, fail_send, b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def settimeout(self, t):
        pass

    def sendall(self, data):
        if self.fail_send and len(self.sent) > 20:
            raise BrokenPipeError("closed by clamd")
        self.sent += data

    def recv(self, n):
        out, self.reply = self.reply, b""
        return out


def test_replies_are_parsed():
    assert parse_reply("stream: OK\0").status == "clean"
    r = parse_reply("stream: Win.Test.EICAR_HDB-1 FOUND\0")
    assert (r.status, r.signature) == ("infected", "Win.Test.EICAR_HDB-1")
    assert parse_reply("INSTREAM size limit exceeded. ERROR\0").status == "too_large"
    assert parse_reply("").status == "unavailable"
    assert parse_reply("UNKNOWN COMMAND").status == "unavailable"


def test_the_whole_file_is_streamed_in_chunks_then_a_zero_chunk(tmp_path: Path):
    f = tmp_path / "deck.pdf"
    body = b"%PDF-1.7\n" + b"x" * (av_scan.CHUNK + 10)
    f.write_bytes(body)
    sock = FakeSock(b"stream: OK\0")
    assert scan_file(f, host="h", port=1, timeout=1, connect=lambda *a, **k: sock).status == "clean"
    assert sock.sent.startswith(b"zINSTREAM\0")
    rest, got = sock.sent[len(b"zINSTREAM\0"):], b""
    while True:
        (n,) = struct.unpack("!L", rest[:4])
        rest = rest[4:]
        if n == 0:
            break
        got, rest = got + rest[:n], rest[n:]
    assert got == body and rest == b""


def test_a_scanner_that_cannot_be_reached_or_drops_the_stream_is_unavailable(tmp_path: Path):
    f = tmp_path / "deck.pdf"
    f.write_bytes(b"%PDF" + b"y" * 100)

    def refuse(*a, **k):
        raise ConnectionRefusedError("no clamd")

    assert scan_file(f, host="h", port=1, timeout=1, connect=refuse).status == "unavailable"
    dropped = FakeSock(b"", fail_send=True)
    assert scan_file(f, host="h", port=1, timeout=1, connect=lambda *a, **k: dropped).status == "unavailable"


@pytest.mark.parametrize("verdict", [
    ScanResult("infected", signature="Eicar"), ScanResult("unavailable", error="down"), ScanResult("too_large", error="limit"),
])
def test_enforce_quarantines_anything_not_clean(monkeypatch, tmp_path, verdict):
    monkeypatch.setattr(settings, "deck_av_scan_mode", "enforce")
    with pytest.raises(DeckQuarantined):
        check_deck(tmp_path / "d.pdf", "d.pdf", "fid", scan=lambda p: verdict)
    assert check_deck(tmp_path / "d.pdf", "d.pdf", "fid", scan=lambda p: ScanResult("clean")).status == "clean"


def test_log_mode_reports_but_lets_the_deck_through(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "deck_av_scan_mode", "log")
    r = check_deck(tmp_path / "d.pdf", "d.pdf", "fid", scan=lambda p: ScanResult("infected", signature="Eicar"))
    assert r.status == "infected"


def test_off_mode_never_scans(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "deck_av_scan_mode", "off")

    def boom(p):
        raise AssertionError("scanned")

    assert check_deck(tmp_path / "d.pdf", "d.pdf", "fid", scan=boom).status == "skipped"


def test_the_sweep_scans_before_extracting_and_caches_nothing_for_a_quarantined_deck(monkeypatch):
    from app.tasks import sync_pitch_decks as sp

    calls = []
    monkeypatch.setattr(settings, "deck_av_scan_mode", "enforce")
    monkeypatch.setattr(sp, "_download_pdf", lambda service, fid, dest: Path(dest).write_bytes(b"%PDF"))
    monkeypatch.setattr(sp.deck_cache, "get", lambda fid, mt: None)
    monkeypatch.setattr(sp.deck_cache, "put", lambda *a: calls.append("cache"))
    monkeypatch.setattr(sp, "extract_text_from_pdf", lambda p: calls.append("extract") or "text")
    monkeypatch.setattr(av_scan, "scan_file", lambda p: calls.append("scan") or ScanResult("infected", signature="Eicar"))
    with pytest.raises(DeckQuarantined):
        sp._download_and_extract(None, {"id": "f1", "name": "deck.pdf", "modifiedTime": "t"})
    assert calls == ["scan"]

    calls.clear()
    monkeypatch.setattr(av_scan, "scan_file", lambda p: calls.append("scan") or ScanResult("clean"))
    assert sp._download_and_extract(None, {"id": "f1", "name": "deck.pdf", "modifiedTime": "t"}) == "text"
    assert calls == ["scan", "extract", "cache"]
