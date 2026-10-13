"""Editable, in-memory text awaiting explicit user confirmation."""

from dataclasses import replace

from app.privacy import ChatLine, ConfirmedSnapshot, build_snapshot, redact_text


class PreviewModel:
    def __init__(self, title: str, lines: list[ChatLine]):
        self._title = str(title)
        self._lines = list(lines)
        self._before_redaction: tuple[str, tuple[ChatLine, ...]] | None = None

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        self._title = str(value)
        self._before_redaction = None

    @property
    def lines(self) -> tuple[ChatLine, ...]:
        return tuple(self._lines)

    def set_text(self, index: int, text: str) -> None:
        self._lines[index] = replace(self._lines[index], text=str(text))
        self._before_redaction = None

    def set_speaker(self, index: int, who: str) -> None:
        if who not in ("her", "me"):
            raise ValueError("说话人必须是 'her' 或 'me'")
        self._lines[index] = replace(self._lines[index], who=who)
        self._before_redaction = None

    def set_name(self, index: int, name: str) -> None:
        self._lines[index] = replace(self._lines[index], name=str(name).strip() or None)
        self._before_redaction = None

    def delete(self, index: int) -> None:
        del self._lines[index]
        self._before_redaction = None

    def append(self, line: ChatLine) -> None:
        self._lines.append(line)
        self._before_redaction = None

    def apply_redaction(self) -> None:
        self._before_redaction = (self._title, self.lines)
        self._title = redact_text(self._title)[0]
        self._lines = [
            replace(line, text=redact_text(line.text)[0],
                    name=redact_text(line.name)[0] if line.name is not None else None)
            for line in self._lines
        ]

    def undo_redaction(self) -> None:
        if self._before_redaction is not None:
            self._title, lines = self._before_redaction
            self._lines = list(lines)
            self._before_redaction = None

    def snapshot(self, relationship: str, context: int, style: str = "",
                 reply_to: str | None = None, reply_goal: str = "") -> ConfirmedSnapshot:
        return build_snapshot(
            self.title, self._lines, relationship, context, style, reply_to, reply_goal,
        )
