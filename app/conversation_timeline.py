"""Display confirmed messages and projected Jev cards; no action authority."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import ScrollArea

from app.privacy import ChatLine
from app.result_view import ResultView, TurnCardView
from app.ui_theme import PALETTE


def _text(text, parent):
    label = QLabel(text, parent)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    label.setFocusPolicy(Qt.StrongFocus)
    label.setAccessibleName(text)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    label.setStyleSheet(f"color: {PALETTE.text}; font-size: 14px; background: transparent;")
    return label


class MessageBubble(QWidget):
    def __init__(self, line: ChatLine, mine: bool, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        bubble = QFrame(self)
        bubble.setObjectName("messageBubble")
        color = PALETTE.recommended if mine else PALETTE.surface
        bubble.setStyleSheet(
            f"QFrame#messageBubble {{ background: {color}; "
            f"border: 1px solid {PALETTE.divider}; border-radius: 10px; }}"
        )
        box = QVBoxLayout(bubble)
        speaker = line.name or ("我" if mine else "对方")
        self.setAccessibleName(f"{speaker}的消息")
        box.addWidget(_text(speaker, bubble))
        box.addWidget(_text(line.text, bubble))
        if mine:
            outer.addStretch(1)
        outer.addWidget(bubble, 5)
        if not mine:
            outer.addStretch(1)


class JevAnalysisCard(QFrame):
    def __init__(self, card: TurnCardView, parent=None):
        super().__init__(parent)
        self.setObjectName("jevAnalysisCard")
        self.setAccessibleName(f"第 {card.message_index + 1} 条消息的 Jev 分析")
        self.setStyleSheet(
            f"QFrame#jevAnalysisCard {{ background: {PALETTE.auxiliary}; "
            f"border: 1px solid {PALETTE.divider}; border-radius: 10px; }}"
        )
        box = QVBoxLayout(self)
        box.addWidget(_text(f"Jev · 第 {card.message_index + 1} 条消息", self))
        for row in card.rows:
            box.addWidget(_text(row, self))
        box.addWidget(_text(card.trend_text, self))
        if card.urgent:
            box.addWidget(_text("紧张度较高，请谨慎回应。", self))


class ConversationTimeline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scroll = ScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(f"QScrollArea {{background: {PALETTE.candidate}; border: none;}}")
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName("已确认对话与 Jev 分析时间线")
        self.scroll.setFocusPolicy(Qt.StrongFocus)
        self.content = QWidget()
        self.content.setObjectName("conversationCanvas")
        self.content.setStyleSheet(f"QWidget#conversationCanvas {{background: {PALETTE.candidate};}}")
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(12, 12, 12, 12)
        self.rows.setSpacing(12)
        self.scroll.setWidget(self.content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.scroll)

    def clear(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Drop sensitive display values before deferred QObject destruction.
                for label in widget.findChildren(QLabel):
                    label.clear()
                    label.setAccessibleName("")
                widget.hide()
                widget.deleteLater()

    def set_conversation(self, lines: tuple[ChatLine, ...], view: ResultView):
        self.clear()
        cards = {card.message_index: card for card in view.turn_cards}
        for index, line in enumerate(lines):
            self.rows.addWidget(MessageBubble(line, mine=line.who == "me"))
            if index in cards:
                self.rows.addWidget(JevAnalysisCard(cards[index]))
        self.rows.addStretch(1)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
