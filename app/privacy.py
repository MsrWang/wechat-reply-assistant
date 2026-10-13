"""Pure privacy transforms and the immutable input boundary for Jev.

This module deliberately has no UI, operating-system, persistence, or network
dependencies.  Callers can redact text while it is still in memory and then
freeze the user-confirmed values in :class:`ConfirmedSnapshot` before handing
them to the model layer.
"""

from dataclasses import dataclass
import re
from typing import Literal


Speaker = Literal["her", "me"]


@dataclass(frozen=True)
class ChatLine:
    who: Speaker
    text: str
    name: str | None = None


@dataclass(frozen=True)
class Redaction:
    kind: str
    original: str
    replacement: str


@dataclass(frozen=True)
class ConfirmedSnapshot:
    title: str
    messages: tuple[ChatLine, ...]
    relationship: str
    context: int
    style: str = ""
    reply_to: str | None = None
    reply_goal: str = ""


_REDACTION_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "email",
        "[邮箱]",
        re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"),
    ),
    (
        "phone",
        "[手机号]",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ),
    (
        "id_card",
        "[身份证号]",
        re.compile(r"(?<!\d)(?:\d{15}|\d{17}[\dXx])(?!\d)"),
    ),
    (
        "bank_card",
        "[银行卡号]",
        re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    ),
)


def redact_text(text: str) -> tuple[str, tuple[Redaction, ...]]:
    """Lightly replace supported sensitive values in *text*.

    Patterns are deliberately applied in the documented order.  Only the
    returned in-memory ``Redaction`` records retain the original values; the
    redacted text contains placeholders instead.
    """

    redactions: list[Redaction] = []
    redacted = str(text)
    for kind, replacement, pattern in _REDACTION_PATTERNS:
        def replace(match: re.Match[str], *, _kind=kind, _replacement=replacement) -> str:
            original = match.group(0)
            redactions.append(Redaction(_kind, original, _replacement))
            return _replacement

        redacted = pattern.sub(replace, redacted)
    return redacted, tuple(redactions)


def build_snapshot(
    title: str,
    lines: list[ChatLine],
    relationship: str,
    context: int,
    style: str = "",
    reply_to: str | None = None,
    reply_goal: str = "",
) -> ConfirmedSnapshot:
    """Normalize and copy user-confirmed chat values into an immutable snapshot."""

    copied_lines: list[ChatLine] = []
    for line in lines:
        who = line.who
        if who not in ("her", "me"):
            raise ValueError(f"说话人必须是 'her' 或 'me'，得到 {who!r}")

        text = str(line.text).strip()
        name = None if line.name is None else str(line.name).strip() or None
        if text:
            copied_lines.append(ChatLine(who, text, name))

    if not copied_lines:
        raise ValueError("至少需要一条非空消息")

    normalized_goal = str(reply_goal).strip()
    if len(normalized_goal) > 500:
        raise ValueError("本次回应目的不能超过 500 个字符")
    normalized_reply_to = None if reply_to is None else str(reply_to).strip() or None
    return ConfirmedSnapshot(
        title=str(title).strip(),
        messages=tuple(copied_lines),
        relationship=str(relationship).strip(),
        context=max(1, min(60, int(context))),
        style=str(style).strip(),
        reply_to=normalized_reply_to,
        reply_goal=normalized_goal,
    )


def to_engine_messages(snapshot: ConfirmedSnapshot) -> list[tuple[str, str, str | None]]:
    """Project a confirmed snapshot into the model engine's message shape."""

    return [(line.who, line.text, line.name) for line in snapshot.messages]
