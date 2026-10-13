"""Process-local identity for the currently visible chat text and area."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import secrets
import unicodedata

from app.privacy import ChatLine


_PROCESS_SECRET = secrets.token_bytes(32)


@dataclass(frozen=True)
class FreshnessToken:
    title_digest: str
    messages_digest: str
    area: tuple[int, int, int, int]


@dataclass(frozen=True)
class RawVisibleChat:
    title: str
    lines: tuple[ChatLine, ...]
    area: tuple[int, int, int, int]
    read_at: datetime
    token: FreshnessToken


def _digest(secret: bytes, value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _stable_text(value: object) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value)).split())


def same_chat_title(left, right):
    """Authorize only strictly equal NFC/whitespace-normalized titles."""
    return _stable_text(left) == _stable_text(right)


def _message_rows(lines):
    return tuple((line.who, _stable_text(line.name or ""), _stable_text(line.text))
                 for line in lines)


def has_new_visible_messages(expected, current):
    """Only a longer, exact normalized baseline prefix proves an append."""
    old_rows, new_rows = _message_rows(expected.lines), _message_rows(current.lines)
    return len(new_rows) > len(old_rows) and new_rows[:len(old_rows)] == old_rows


def make_raw_visible(title, lines, area, *, secret=None, now=None):
    """Freeze OCR values and build salted, process-local freshness digests."""
    key = secret or _PROCESS_SECRET
    frozen = tuple(lines)
    rect = tuple(int(value) for value in area[:4])
    message_rows = _message_rows(frozen)
    token = FreshnessToken(
        _digest(key, str(title)),
        _digest(key, message_rows),
        rect,
    )
    read_at = now or datetime.now().astimezone()
    if read_at.tzinfo is None or read_at.utcoffset() is None:
        read_at = read_at.astimezone()
    return RawVisibleChat(str(title), frozen, rect, read_at, token)


def same_visible_chat(expected, current):
    """Return whether title, message sequence, and visible area still match."""
    if expected.area != current.area or not same_chat_title(expected.title, current.title):
        return False
    if expected.token.messages_digest == current.token.messages_digest:
        return True
    return _message_rows(expected.lines) == _message_rows(current.lines)
