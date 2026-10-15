"""
Antivirus scan for inbound pitch decks (Raed security audit 2026-10-15, R-deal-flow).

The Drive "Inbound Pitch Decks" folder holds email attachments from anyone, and
the sweep used to hand every one straight to the PDF parsers. Each downloaded
file now streams through ClamAV first: the shared clamd that already scans the
Raed application form's decks and Reem's uploads (raed-ai-clamd on the
raed_platform network, INSTREAM over TCP 3310).

DECK_AV_SCAN_MODE:
  off      no scan.
  log      scan and log the verdict; extraction goes ahead whatever it says.
           The rollout mode: it shows what enforce would refuse first.
  enforce  an infected file is quarantined (never parsed, never stored on a
           lead), and so is a file that could not be scanned (clamd down,
           timed out, or the file over clamd's 50 MB stream limit). Nothing is
           cached for it, so the next sweep tries again.
"""
from __future__ import annotations

import logging
import re
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app.config import settings

logger = logging.getLogger(__name__)

CHUNK = 64 * 1024


@dataclass(frozen=True)
class ScanResult:
    status: str  # "clean" | "infected" | "too_large" | "unavailable" | "skipped"
    signature: Optional[str] = None
    error: Optional[str] = None


class DeckQuarantined(Exception):
    """Raised in enforce mode for a file that must not be parsed."""

    def __init__(self, name: str, result: ScanResult):
        self.result = result
        what = result.signature or result.error or result.status
        super().__init__(f"deck {name!r} quarantined: {result.status} ({what})")


def parse_reply(reply: str) -> ScanResult:
    r = reply.replace("\0", "").strip()
    if re.fullmatch(r"stream: OK", r):
        return ScanResult("clean")
    found = re.fullmatch(r"stream: (.+) FOUND", r)
    if found:
        return ScanResult("infected", signature=found.group(1))
    if "size limit exceeded" in r.lower():
        return ScanResult("too_large", error=r)
    return ScanResult("unavailable", error=r or "empty clamd reply")


def scan_file(
    path: Path,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: Optional[float] = None,
    connect: Callable[..., socket.socket] = socket.create_connection,
) -> ScanResult:
    """Stream one file to clamd (zINSTREAM). Never raises: a failure is 'unavailable'."""
    host = host or settings.clamd_host
    port = port or settings.clamd_port
    timeout = timeout or settings.clamd_timeout_seconds
    try:
        with connect((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"zINSTREAM\0")
            with open(path, "rb") as fh:
                while True:
                    block = fh.read(CHUNK)
                    if not block:
                        break
                    sock.sendall(struct.pack("!L", len(block)) + block)
            sock.sendall(struct.pack("!L", 0))
            reply = b""
            while True:
                part = sock.recv(4096)
                if not part:
                    break
                reply += part
                if b"\0" in part:
                    break
        return parse_reply(reply.decode("utf-8", "replace"))
    except (OSError, ValueError) as exc:
        # A send that fails mid-stream is usually clamd closing on the size limit;
        # either way the file was not scanned.
        return ScanResult("unavailable", error=f"{type(exc).__name__}: {exc}")


def check_deck(path: Path, name: str, file_id: str, *, scan: Optional[Callable[[Path], ScanResult]] = None) -> ScanResult:
    """Scan a downloaded deck per DECK_AV_SCAN_MODE. Raises DeckQuarantined only in enforce mode."""
    mode = (settings.deck_av_scan_mode or "log").strip().lower()
    if mode == "off":
        return ScanResult("skipped")
    result = (scan or scan_file)(path)
    if result.status == "clean":
        logger.info("deck av scan: clean file_id=%s", file_id)
        return result
    logger.warning(
        "deck av scan: %s file_id=%s signature=%s error=%s mode=%s",
        result.status, file_id, result.signature, result.error, mode,
    )
    if mode == "enforce":
        raise DeckQuarantined(name, result)
    return result
