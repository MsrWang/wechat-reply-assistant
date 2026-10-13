# -*- coding: utf-8 -*-
"""浅色置顶回复助手：回复建议和独立设置页。发送始终由用户在微信确认。"""
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QButtonGroup, QFocusFrame, QFrame, QHBoxLayout, QLabel,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget, QLineEdit, QMessageBox, QPushButton,
)
from qfluentwidgets import (
    BodyLabel, CardWidget, CheckBox, ComboBox, FluentIcon as FIF,
    IndeterminateProgressBar, LineEdit, PasswordLineEdit, PlainTextEdit,
    PrimaryPushButton, PushButton, ScrollArea, Theme, TransparentToolButton,
    setCustomStyleSheet, setFont, setTheme, setThemeColor,
)

from app import settings
from app.conversation_timeline import ConversationTimeline
from app.version import VERSION
from app.preview_model import PreviewModel
from app.privacy import ChatLine, ConfirmedSnapshot
from app.result_view import ReplyView, ResultView, project_result, project_unreviewed_candidates
from app.ui_theme import PALETTE, configure_text_fonts
from app.reply_editor import ReplyEditorMixin
from app.desktop_workspace import WorkspaceSplitter

_MUTED = PALETTE.muted
_GREEN = PALETTE.primary
_RELATIONSHIPS = [
    ("日常交流", "everyday conversation"),
    ("恋人", "romantic partners"), ("朋友", "friends"), ("同事", "colleagues"),
    ("家人", "family"), ("自定义", None),
]

def _label(text="", size=14, color=None, bold=False, parent=None):
    label = BodyLabel(text, parent)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    setFont(label, size, QFont.DemiBold if bold else QFont.Normal)
    if color:
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(label, qss, qss)
    return label


def _tool(icon, title, callback, parent=None):
    button = TransparentToolButton(icon, parent)
    button.setFixedSize(32, 32)
    button.setToolTip(title)
    button.setAccessibleName(title)
    button.clicked.connect(callback)
    return button


class _Surface(CardWidget):
    def __init__(self, parent=None, accent=False):
        self.accent = accent
        super().__init__(parent)
        self.setBorderRadius(12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def _normalBackgroundColor(self):
        return QColor(PALETTE.recommended if self.accent else PALETTE.surface)

    def _hoverBackgroundColor(self):
        return self._normalBackgroundColor()

    def _pressedBackgroundColor(self):
        return self._normalBackgroundColor()


class _MainWindow(QWidget):
    """窗口大小变了就叫 Overlay 重新排布；断点没跨过时 _relayout 自己不做事，这里不用防抖。"""
    def __init__(self, relayout):
        super().__init__()
        self._relayout = relayout

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout(event.size().width(), event.size().height())


class _ReplyCard(QWidget):
    def __init__(self, owner, reply: ReplyView, number=1):
        super().__init__()
        self.reply = reply
        self.setObjectName("recommendedReply" if reply.recommended else "candidateReply")
        surface = PALETTE.recommended if reply.recommended else PALETTE.candidate
        self.setStyleSheet(
            f"QWidget#{self.objectName()} {{ background: {surface}; "
            f"border: 1px solid {PALETTE.divider}; border-radius: 12px; }}"
            f"QLabel#scoreBadge {{ color: #1266C3; background: {PALETTE.auxiliary}; "
            "border-radius: 10px; padding: 2px 8px; }"
        )
        self.setAttribute(Qt.WA_StyledBackground, True)
        box = QVBoxLayout(self)
        self.box = box
        box.setSpacing(10)
        top = QHBoxLayout()
        labels = {
            "recommended": "推荐回复",
            "fallback": "安全备选",
            "other": f"候选 {number}" if reply.safe_to_send else f"未采用候选 {number}",
        }
        label = labels[reply.role]
        self.originalLabel = label
        self.roleLabel = _label(label, 12, PALETTE.primary if reply.recommended else PALETTE.muted, True)
        top.addWidget(self.roleLabel, 1)
        self.scoreBadge = QLabel(reply.percent_text or "", self)
        self.scoreBadge.setObjectName("scoreBadge")
        self.scoreBadge.setAccessibleName(
            f"相对推荐分 {reply.percent_text}" if reply.percent_text else "未提供相对推荐分"
        )
        self.scoreBadge.setVisible(reply.percent_text is not None)
        top.addWidget(self.scoreBadge)
        self.copyButton = PushButton("复制", self)
        self.copyButton.setAccessibleName("复制这条回复")
        self.copyButton.clicked.connect(lambda: owner._copy(reply.source_index))
        top.addWidget(self.copyButton)
        box.addLayout(top)
        self.text = _label(reply.text, 15, PALETTE.text)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.text.setFocusPolicy(Qt.StrongFocus)
        self.text.setAccessibleName(f"第 {number} 条候选回复正文")
        box.addWidget(self.text)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        button_type = PushButton if reply.role == "fallback" else PrimaryPushButton
        button_text = "仍要填入微信" if reply.role == "fallback" else "填入微信（追加）"
        self.fillButton = button_type(button_text, self)
        self.fillButton.setAccessibleName(
            f"仍要把第 {number} 条安全备选追加到微信输入框"
            if reply.role == "fallback"
            else f"追加第 {number} 条推荐回复到微信输入框"
        )
        self.fillButton.setToolTip("会追加到微信当前输入框末尾；如果已有草稿，请先在微信中整理。")
        self.fillButton.clicked.connect(lambda: owner._fill(reply.source_index))
        self.fillButton.setVisible(reply.fill_allowed and reply.role != "other")
        self.fillButton.hide()
        self.selectButton = PushButton("选用这条", self)
        self.selectButton.clicked.connect(lambda: owner.select_reply(reply.source_index))
        bottom.addWidget(self.selectButton)
        for button in (self.copyButton, self.fillButton):
            button.setFocusPolicy(Qt.StrongFocus)
        bottom.addWidget(self.fillButton)
        box.addLayout(bottom)
        self.set_compact(owner._compact)

    def set_fill_available(self, enabled: bool) -> None:
        self.fillButton.setEnabled(bool(enabled and self.reply.fill_allowed))

    def set_available(self, enabled):
        self.set_fill_available(enabled)
        self.copyButton.setEnabled(bool(enabled and self.reply.copy_allowed))
        self.selectButton.setEnabled(bool(enabled and self.reply.safe_to_send))

    def set_compact(self, compact):
        if compact:
            self.box.setContentsMargins(12, 12, 12, 14)
        else:
            self.box.setContentsMargins(16, 14, 16, 16)
        self.fillButton.setMinimumWidth(80 if compact else 100)


class Overlay(ReplyEditorMixin):
    """Manual UI; callbacks own capture, analysis, fill and session cleanup."""

    def __init__(self, on_read, on_analyze, on_fill, on_clear, on_save_key, on_forget_key,
                 on_key_status, on_rewrite=None):
        self.app = QApplication.instance() or QApplication([])
        configure_text_fonts(self.app)
        setTheme(Theme.LIGHT)
        setThemeColor(_GREEN, save=False)
        self.on_read = on_read
        self.on_analyze = on_analyze
        self.on_fill = on_fill
        self.on_clear = on_clear
        self.on_save_key = on_save_key
        self.on_forget_key = on_forget_key
        self.on_key_status = on_key_status
        self.on_rewrite = on_rewrite
        self._demo = False
        self._saved_key_present = None
        self.preview = PreviewModel("", [])
        self.cands = []
        self.cards = []
        self._reply_views = {}
        self._previewRows = []
        self._busy = False
        self._current = False
        self._fill_consumed = False
        self._analysis_source = "user_confirmed_only"
        self._compact = None
        self._pageLayouts = []
        self._hintLabels = []
        self.win = _MainWindow(self._relayout)
        self.win.setObjectName("assistantWindow")
        self.win.setWindowTitle("Jev · 微信回复助手")
        self.win.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.win.setStyleSheet(
            f"QWidget#assistantWindow {{ background: {PALETTE.window}; "
            f"border: 1px solid {PALETTE.divider}; border-radius: 14px; }}"
        )
        self.win.setMinimumWidth(720)
        outer = QHBoxLayout(self.win)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setStyleSheet(f"QWidget#sidebar {{background: {PALETTE.sidebar};}}")
        navigation = QVBoxLayout(self.sidebar)
        navigation.setContentsMargins(12, 24, 12, 16)
        navigation.setSpacing(12)
        navigation.addWidget(_label("Jev", 26, PALETTE.sidebar_text, True))
        self.subtitle = _label("微信回复助手", 12, PALETTE.sidebar_text)
        navigation.addWidget(self.subtitle)
        navigation.addSpacing(20)
        self.workspaceButton = QPushButton(FIF.HOME.icon(Theme.DARK), "工作区")
        self.workspaceButton.clicked.connect(self._back_home)
        self.navDemoButton = QPushButton(FIF.PLAY.icon(Theme.DARK), "示例体验")
        self.navDemoButton.clicked.connect(self.show_demo)
        self.settingsButton = QPushButton(FIF.SETTING.icon(Theme.DARK), "设置")
        self.settingsButton.clicked.connect(self.open_settings)
        self.navButtons = (self.workspaceButton, self.navDemoButton, self.settingsButton)
        for button, name in zip(self.navButtons, ("工作区", "示例体验", "设置")):
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.setMinimumHeight(40)
            button.setStyleSheet(f"QPushButton {{color: {PALETTE.sidebar_text}; background: transparent; border: none; text-align: left; padding: 8px; font-size: 14px;}} QPushButton:hover, QPushButton:checked {{background: {PALETTE.sidebar_active}; border-radius: 6px;}}")
            button.setCheckable(True)
            navigation.addWidget(button)
        self.workspaceButton.setChecked(True)
        navigation.addStretch(1)
        self.connectionLabel = _label("连接未设置", 11, PALETTE.sidebar_text)
        navigation.addWidget(self.connectionLabel)
        self.versionLabel = _label(f"Jev · v{VERSION}", 11, _MUTED)
        outer.addWidget(self.sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(main, 1)
        self.pages = QStackedWidget(self.win)
        main.addWidget(self.pages, 1)
        self._build_home()
        self._build_settings()
        self._name_controls(self.win)
        self.focusFrame = QFocusFrame(self.win)
        self.focusFrame.setObjectName("keyboardFocusFrame")
        self.focusFrame.setStyleSheet(
            f"QFocusFrame#keyboardFocusFrame {{ border: 2px solid {PALETTE.primary}; "
            "border-radius: 7px; background: transparent; }"
        )
        self.focusFrame.hide()
        self.app.focusChanged.connect(self._update_focus_frame)
        screen = self.app.primaryScreen().availableGeometry()
        self.win.setMinimumHeight(620)
        self.win.resize(min(1120, screen.width() - 32), min(800, screen.height() - 48))
        self.win.move(screen.right() - self.win.width() - 20, screen.top() + 24)
        self._relayout(self.win.width(), self.win.height())
        self._sync_actions()
        try:
            self._saved_key_present = bool(self.on_key_status())
        except Exception:
            self._saved_key_present = None
        self._sync_actions()
        self.set_status("读取聊天，确认文字，再生成回复")
        self.win.show()

    def _update_focus_frame(self, previous, current):
        if current is None or not (current is self.win or self.win.isAncestorOf(current)):
            self.focusFrame.setWidget(None)
            self.focusFrame.hide()
            return
        self.focusFrame.setWidget(current)
        self.focusFrame.show()
        self.focusFrame.raise_()

    @staticmethod
    def _name_controls(parent):
        for button in parent.findChildren(QAbstractButton):
            if not button.accessibleName():
                button.setAccessibleName(button.text() or button.toolTip() or "显示或隐藏内容")
            button.setFocusPolicy(Qt.StrongFocus)

    def _scroll_page(self):
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName("pageContent")
        content.setStyleSheet("QWidget#pageContent { background: transparent; }")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 8, 20, 12)
        layout.setSpacing(14)
        scroll.setWidget(content)
        self.pages.addWidget(scroll)
        self._pageLayouts.append(layout)
        return scroll, layout

    def _relayout(self, w, h):
        if not hasattr(self, "workspaceSplitter"):
            return
        compact = w < 1000
        if compact != self._compact:
            self._compact = compact
            self.subtitle.setVisible(not compact)
            self.sidebar.setFixedWidth(64 if compact else 168)
            self.connectionLabel.setVisible(not compact)
            for button, text in zip(self.navButtons, ("工作区", "示例体验", "设置")):
                button.setText("" if compact else text)
            self.workspaceSplitter.set_compact(compact)
            self.home.layout().setSpacing(8 if compact else 16)
            self._set_other_replies_visible(self.unusedToggle.isChecked())
            self.focusTabs.setVisible(compact and bool(self.preview.lines or self.cands))
            for label in self._hintLabels:
                label.setVisible(not compact)
            margins = (12, 16, 12, 8) if compact else (24, 24, 24, 8)
            for layout in self._pageLayouts:
                layout.setContentsMargins(*margins)
            for card in self.cards:
                card.set_compact(compact)

    def _build_home(self):
        self.home = QWidget()
        body = QVBoxLayout(self.home)
        body.setContentsMargins(24, 24, 24, 8)
        body.setSpacing(16)
        self._pageLayouts.append(body)
        self.pages.addWidget(self.home)
        self.headerPanel = QWidget()
        header = QVBoxLayout(self.headerPanel)
        header.setContentsMargins(0, 0, 0, 0)
        heading = QHBoxLayout()
        self.chatTitleLabel = _label("未确认会话", 18, PALETTE.text, True)
        self.chatTitleLabel.setWordWrap(False)
        self.chatTitleLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        heading.addWidget(self.chatTitleLabel, 1)
        self.readTimeLabel = _label("尚未读取", 11, _MUTED)
        self.readTimeLabel.hide()
        self.updated = _label("", 11, _MUTED)
        self.updated.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.updated.hide()
        self.readButton = PrimaryPushButton("读取当前聊天")
        self.readButton.clicked.connect(self._read)
        heading.addWidget(self.readButton)
        header.addLayout(heading)
        self.status = _label("", 12, _MUTED)
        header.addWidget(self.status)
        self.mediaLimit = _label("只读取到当前画面的文字；语音、图片、表情和屏幕外内容需要你补充。", 11, _MUTED)
        self.mediaLimit.hide()
        self.progress = IndeterminateProgressBar()
        self.progress.setFixedHeight(3)
        self.progress.hide()
        header.addWidget(self.progress)
        body.addWidget(self.headerPanel)
        self.welcomePanel = _Surface()
        self.welcomePanel.setMinimumWidth(560)
        self.welcomePanel.setMaximumWidth(680)
        welcome = QVBoxLayout(self.welcomePanel)
        welcome.addWidget(_label("把话说得更合适", 20, PALETTE.text, True))
        welcome.addWidget(_label("读懂对话，再写下你想说的话。\n检查消息 → 生成草稿 → 修改后手动发送", 15, _MUTED))
        self.welcomeReadButton = PrimaryPushButton("读取当前聊天，开始起草")
        self.welcomeReadButton.clicked.connect(self._read)
        welcome.addWidget(self.welcomeReadButton, 0, Qt.AlignLeft)
        welcome.setContentsMargins(32, 32, 32, 32)
        welcome.setSpacing(20)
        welcome_actions = QHBoxLayout()
        self.connectButton = PushButton("设置 OpenRouter Key")
        self.connectButton.clicked.connect(self.open_settings)
        self.demoButton = PushButton("试用示例 · 离线")
        self.demoButton.clicked.connect(self.show_demo)
        self.manualButton = PushButton("手动输入")
        self.manualButton.clicked.connect(self._append_line)
        for button in (self.connectButton, self.demoButton, self.manualButton):
            welcome_actions.addWidget(button)
        welcome.addLayout(welcome_actions)
        self.welcomeSpace = QWidget()
        body.addWidget(self.welcomeSpace, 1)
        body.addWidget(self.welcomePanel, 0, Qt.AlignHCenter)
        self.welcomeBottomSpace = QWidget()
        body.addWidget(self.welcomeBottomSpace, 2)

        self.focusTabs = QWidget()
        tab_row = QHBoxLayout(self.focusTabs)
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(0)
        self.analysisTab = PushButton("对话分析")
        self.replyTab = PushButton("回复建议")
        self.analysisTab.setCheckable(True)
        self.replyTab.setCheckable(True)
        self.viewGroup = QButtonGroup(self.focusTabs)
        self.viewGroup.setExclusive(True)
        tab_qss = (
            f"QPushButton {{ color: {PALETTE.muted}; background: transparent; "
            f"border: none; border-bottom: 1px solid {PALETTE.divider}; }}"
            f"QPushButton:checked {{ color: {PALETTE.primary}; font-weight: 600; "
            f"border-bottom: 3px solid {PALETTE.primary}; }}"
        )
        for button in (self.analysisTab, self.replyTab):
            button.setMinimumHeight(36)
            button.setFocusPolicy(Qt.StrongFocus)
            button.setStyleSheet(tab_qss)
            tab_row.addWidget(button, 1)
            self.viewGroup.addButton(button)
        self.analysisTab.setAccessibleName("对话分析视图")
        self.analysisTab.clicked.connect(lambda: self._select_home_view(0, focus_content=True))
        self.replyTab.clicked.connect(lambda: self._select_home_view(1, focus_content=True))
        body.addWidget(self.focusTabs)

        self.focusPages = WorkspaceSplitter()
        self.workspaceSplitter = self.focusPages
        self.focusPages.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body.addWidget(self.focusPages, 1)

        self.centerPages = QStackedWidget()
        self.centerPages.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.analysisView = QWidget()
        self.analysisView.setObjectName("analysisView")
        self.analysisView.setStyleSheet(
            f"QWidget#analysisView {{ background: {PALETTE.surface}; }}"
        )
        analysis_layout = QVBoxLayout(self.analysisView)
        analysis_layout.setContentsMargins(16, 16, 16, 16)
        analysis_layout.setSpacing(12)
        analysis_layout.addWidget(_label("对话与分析", 18, PALETTE.text, True))
        self.previewContext = _label("", 12, PALETTE.muted)
        self.previewContext.setAccessibleName("本次分析背景")
        self.previewContext.hide()
        analysis_layout.addWidget(self.centerPages, 1)
        self.focusPages.addWidget(self.analysisView)
        self.previewScroll = ScrollArea()
        self.previewScroll.setWidgetResizable(True)
        self.previewScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.previewScroll.setFrameShape(QFrame.NoFrame)
        self.previewScroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.previewScroll.setAccessibleName("发送前预览")
        self.previewScroll.setFocusPolicy(Qt.StrongFocus)
        preview_content = QWidget()
        preview_content.setStyleSheet(f"background: {PALETTE.surface};")
        preview_layout = QVBoxLayout(preview_content)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.previewScroll.setWidget(preview_content)
        self.centerPages.addWidget(self.previewScroll)
        self.timeline = ConversationTimeline()
        self.centerPages.addWidget(self.timeline)

        self.previewPanel = _Surface()
        preview_box = QVBoxLayout(self.previewPanel)
        preview_box.setContentsMargins(14, 12, 14, 12)
        preview_box.setSpacing(10)
        preview_box.addWidget(_label("发送前预览", 16, PALETTE.text, True))
        self.editContextButton = PushButton("修改关系和口吻")
        self.editContextButton.setAccessibleName("修改本次关系和说话风格")
        self.editContextButton.setCheckable(True)
        self.editContextButton.toggled.connect(lambda shown: self.chatPreferences.setVisible(shown))
        preview_box.addWidget(self.editContextButton)
        self.preferenceSlot = QVBoxLayout()
        preview_box.addLayout(self.preferenceSlot)
        preview_box.addWidget(_label("会话标题", 12, _MUTED))
        self.titleEdit = LineEdit()
        self.titleEdit.setAccessibleName("预览会话标题")
        self.titleEdit.setPlaceholderText("读取后可编辑，也可手动填写")
        self.titleEdit.textChanged.connect(self._title_changed)
        preview_box.addWidget(self.titleEdit)
        self.previewBox = QVBoxLayout()
        self.previewBox.setSpacing(10)
        preview_box.addLayout(self.previewBox)
        actions = QHBoxLayout()
        self.addButton = PushButton("添加一条")
        self.addButton.clicked.connect(self._append_line)
        actions.addWidget(self.addButton)
        self.redactButton = PushButton("轻量脱敏")
        self.redactButton.clicked.connect(self._redact)
        actions.addWidget(self.redactButton)
        self.undoButton = PushButton("撤销脱敏")
        self.undoButton.clicked.connect(self._undo_redaction)
        actions.addWidget(self.undoButton)
        preview_box.addLayout(actions)
        self.targetRow = QWidget()
        target_box = QVBoxLayout(self.targetRow)
        target_box.setContentsMargins(0, 0, 0, 0)
        target_box.addWidget(_label("回复对象", 12, _MUTED))
        self.targetEdit = LineEdit()
        self.targetEdit.setAccessibleName("回复对象")
        self.targetEdit.setPlaceholderText("可选；填写预览中的发言人")
        self.targetEdit.textChanged.connect(self._preview_changed)
        target_box.addWidget(self.targetEdit)
        preview_box.addWidget(self.targetRow)
        self.targetRow.hide()
        preview_layout.addWidget(self.previewPanel)
        self.modeHint = _label("将按回复模式分析", 12, PALETTE.primary, True)
        self.modeHint.setAccessibleName("本次分析模式")
        preview_layout.addWidget(self.modeHint)
        self.replyGoalEdit = LineEdit()
        self.replyGoalEdit.setMaxLength(500)
        self.replyGoalEdit.setAccessibleName("这次想怎么回应或继续，可选")
        self.replyGoalEdit.setPlaceholderText("例如：先肯定对方，再问清楚时间")
        self.replyGoalEdit.textChanged.connect(self._preview_changed)
        analysis_layout.addWidget(self.replyGoalEdit)
        self.sourceHint = _label(
            "先试用离线示例，或读取你自己的聊天。",
            12, PALETTE.muted,
        )
        self.sourceHint.setAccessibleName("本次内容来源能力")
        self.sourceHint.hide()
        self.confirmation = _label(
            "将已确认的可见文本、关系、口吻、这次回复目标和回复对象发送至 OpenRouter，"
            "先起草候选，再由 Jev 判断；本次最多两次请求。",
            12, _MUTED,
        )
        self.confirmation.hide()
        self.analyzeButton = PrimaryPushButton("生成回复")
        self.analyzeButton.setEnabled(False)
        self.analyzeButton.clicked.connect(self._analyze)
        analysis_layout.addWidget(self.analyzeButton)
        preview_layout.addStretch(1)

        self.replyView = QWidget()
        reply_view_layout = QVBoxLayout(self.replyView)
        self.replyView.setObjectName("replyView")
        self.replyView.setStyleSheet(f"QWidget#replyView {{background: {PALETTE.surface};}}")
        reply_view_layout.setContentsMargins(16, 16, 16, 16)
        reply_view_layout.setSpacing(12)
        reply_view_layout.addWidget(_label("回复草稿", 18, PALETTE.text, True))
        self.replyScroll = ScrollArea()
        self.replyScroll.setWidgetResizable(True)
        self.replyScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.replyScroll.setFrameShape(QFrame.NoFrame)
        self.replyScroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.replyScroll.viewport().setAutoFillBackground(False)
        self.replyScroll.setAccessibleName("回复建议，可滚动查看")
        self.replyScroll.setFocusPolicy(Qt.StrongFocus)
        reply_canvas = QWidget()
        reply_canvas.setObjectName("replyCanvas")
        reply_canvas.setStyleSheet(
            f"QWidget#replyCanvas {{ background: {PALETTE.surface}; }}"
        )
        dock = QVBoxLayout(reply_canvas)
        dock.setContentsMargins(0, 4, 0, 8)
        dock.setSpacing(12)
        self.replyScroll.setWidget(reply_canvas)
        self.focusPages.addWidget(self.replyView)
        self.replyStatus = _label("未生成回复", 13, PALETTE.text, True)
        self.actionLabel = _label("", 12, _MUTED)
        self.reasonLabel = _label("", 12, PALETTE.muted)
        self.fallbackLabel = _label("如果你仍想礼貌回应", 12, PALETTE.text, True)
        dock.addWidget(self.replyStatus)
        dock.addWidget(self.actionLabel)
        dock.addWidget(self.reasonLabel)
        dock.addWidget(self.fallbackLabel)
        self.reasonLabel.hide()
        self.fallbackLabel.hide()
        self.unusedToggle = PushButton("查看其他备选")
        self.unusedToggle.setStyleSheet(f"QPushButton {{border: none; color: {PALETTE.primary}; background: transparent; padding: 4px; text-align: left;}} QPushButton:hover {{background: {PALETTE.recommended};}}")
        self.unusedToggle.setCheckable(True)
        self.unusedToggle.hide()
        self.replyLayout = reply_view_layout
        self.build_reply_editor(reply_view_layout)
        reply_view_layout.addWidget(self.replyScroll, 1)
        self.primaryReplyContent = QWidget()
        self.primaryReplyContent.setAccessibleName("本轮首选回复")
        self.primaryReplyBox = QVBoxLayout(self.primaryReplyContent)
        self.primaryReplyBox.setContentsMargins(0, 0, 0, 0)
        self.primaryReplyBox.setSpacing(10)
        dock.addWidget(self.primaryReplyContent)
        self.primaryReplyContent.hide()
        dock.addWidget(self.unusedToggle)

        self.otherReplyContent = QWidget()
        self.otherReplyContent.setAccessibleName("其他候选回复列表")
        self.otherReplyBox = QVBoxLayout(self.otherReplyContent)
        self.otherReplyBox.setContentsMargins(0, 0, 0, 0)
        self.otherReplyBox.setSpacing(10)
        self.unusedToggle.toggled.connect(self._set_other_replies_visible)
        dock.addWidget(self.otherReplyContent)
        self.otherReplyContent.hide()
        self._other_toggle_open_text = "查看其他备选"
        self._other_toggle_close_text = "收起其他备选"

        self.empty = _Surface()
        empty_box = QVBoxLayout(self.empty)
        empty_box.setContentsMargins(18, 20, 18, 20)
        self.emptyTitle = _label("读取后确认文本，再开始分析", 16, PALETTE.text, True)
        empty_box.addWidget(self.emptyTitle)
        self.emptyHint = _label("也可以添加消息，手动整理本次预览。", 13, _MUTED)
        empty_box.addWidget(self.emptyHint)
        self.setupButton = PushButton("前往设置")
        self.setupButton.clicked.connect(self.open_settings)
        empty_box.addWidget(self.setupButton, 0, Qt.AlignHCenter)
        dock.addWidget(self.empty)
        self.clearButton = PushButton("清除本次内容")
        self.clearButton.clicked.connect(self._clear)
        self.clearButton.setFixedWidth(108)
        self.clearButton.setStyleSheet(f"QPushButton {{border: none; background: transparent; color: {PALETTE.muted};}} QPushButton:hover {{background: {PALETTE.candidate};}}")
        heading.insertWidget(1, self.clearButton)
        dock.addStretch(1)
        self._update_reply_tab(0)
        self._select_home_view(0)

    def _select_home_view(self, index: int, *, focus_content: bool = False) -> None:
        index = 1 if index == 1 else 0
        self.focusPages.setCurrentIndex(index)
        self.analysisTab.setChecked(index == 0)
        self.replyTab.setChecked(index == 1)
        if focus_content:
            if index == 1:
                target = self.replyScroll
            elif self.centerPages.currentWidget() is self.timeline:
                target = self.timeline.scroll
            else:
                target = self.centerPages.currentWidget()
            QTimer.singleShot(0, target.setFocus)

    def _update_reply_tab(self, count: int) -> None:
        count = max(0, int(count))
        self.replyTab.setText(f"回复建议 {count}" if count else "回复建议")
        self.replyTab.setAccessibleName(
            f"回复建议，{count} 条" if count else "回复建议，尚未生成"
        )

    def _build_settings(self):
        self.settingsPage, body = self._scroll_page()
        self.settingsPage.widget().setMaximumWidth(700)
        heading = QHBoxLayout()
        heading.addWidget(_tool(FIF.RETURN, "返回回复建议", self._back_home))
        heading.addWidget(_label("设置", 23, PALETTE.text, True), 1)
        heading.addWidget(self.versionLabel)
        body.addLayout(heading)
        preference = _Surface()
        box = QVBoxLayout(preference)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)
        box.addWidget(_label("回复偏好", 16, PALETTE.text, True))
        box.addWidget(_label("你们的关系", 13))
        self.relationshipBox = ComboBox()
        self.relationshipBox.setMinimumWidth(0)
        self.relationshipBox.addItems([name for name, value in _RELATIONSHIPS])
        self.relationshipBox.setAccessibleName("你们的关系")
        box.addWidget(self.relationshipBox)
        self.relEdit = LineEdit()
        self.relEdit.setPlaceholderText("例如：刚认识的朋友，正在慢慢熟悉")
        self.relEdit.setAccessibleName("自定义关系背景")
        box.addWidget(self.relEdit)
        box.addWidget(_label("说话风格（可选）", 13))
        self.styleEdit = LineEdit()
        self.styleEdit.setPlaceholderText("例如：话少、不用标点")
        self.styleEdit.setAccessibleName("说话风格")
        box.addWidget(self.styleEdit)
        box.addWidget(self._hint("分析使用全部已确认文字；最多展示最近 10 条对方消息的详细卡。"))
        self.targetCheck = CheckBox("群聊指定回复对象")
        box.addWidget(self.targetCheck)
        self.chatPreferences = preference
        self.preferenceSlot.addWidget(preference)
        preference.hide()

        connection = _Surface()
        box = QVBoxLayout(connection)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)
        box.addWidget(_label("OpenRouter Key", 16, PALETTE.text, True))
        self.keyEdit = PasswordLineEdit()
        self.keyEdit.setEchoMode(QLineEdit.Password)
        self.keyEdit.setAccessibleName("OpenRouter Key")
        self.keyEdit.setPlaceholderText("临时使用，或选择保存在这台电脑上")
        box.addWidget(self.keyEdit)
        self.keyState = _label("未设置", 12, _MUTED)
        box.addWidget(self.keyState)
        self.rememberCheck = CheckBox("在这台电脑上记住 Key")
        box.addWidget(self.rememberCheck)
        self.forgetKeyButton = PushButton("忘记已保存 Key")
        self.forgetKeyButton.clicked.connect(self._forget_key)
        box.addWidget(self.forgetKeyButton)
        box.addWidget(self._hint(
            "临时 Key 在清除本次内容或退出时移除；保存后由 Windows 凭据管理器保管。"
        ))
        body.addWidget(connection)
        self.settingsFeedback = _label("", 13, _GREEN)
        self.settingsFeedback.hide()
        body.addWidget(self.settingsFeedback)
        actions = QHBoxLayout()
        actions.addStretch(1)
        back = PushButton("返回")
        back.clicked.connect(self._back_home)
        actions.addWidget(back)
        self.saveButton = PrimaryPushButton("保存并返回")
        self.saveButton.clicked.connect(self._save)
        actions.addWidget(self.saveButton)
        body.addLayout(actions)
        body.addStretch(1)
        self._load_settings()
        self.relationshipBox.currentIndexChanged.connect(self._relationship_changed)
        self.relEdit.textChanged.connect(self._preview_changed)
        self.styleEdit.textChanged.connect(self._preview_changed)
        self.targetCheck.stateChanged.connect(self._target_option_changed)
        self.keyEdit.textChanged.connect(self._key_changed)
        self._refresh_key_state()

    def _hint(self, text):
        label = _label(text, 12, _MUTED)
        self._hintLabels.append(label)
        return label

    def _load_settings(self):
        settings.migrate_legacy_preferences()
        preferences = settings.load_preferences()
        relationship = preferences.relationship
        index = next((i for i, (_, value) in enumerate(_RELATIONSHIPS) if value == relationship),
                     len(_RELATIONSHIPS) - 1)
        self.relationshipBox.setCurrentIndex(index)
        self.relEdit.setText(relationship if _RELATIONSHIPS[index][1] is None else "")
        self.relEdit.setVisible(_RELATIONSHIPS[index][1] is None)
        self.styleEdit.setText(preferences.style)
        self._compatibility_context = preferences.context
        self.targetCheck.setChecked(preferences.reply_target)
        self._sync_target_row()

    def _relationship(self):
        return _RELATIONSHIPS[self.relationshipBox.currentIndex()][1] or self.relEdit.text().strip()

    def _relationship_changed(self, index):
        self.relEdit.setVisible(_RELATIONSHIPS[index][1] is None)
        self._preview_changed()

    def _target_option_changed(self, *args):
        self._sync_target_row()
        if not self.targetCheck.isChecked():
            self.targetEdit.setText("")
        self._preview_changed()
        for row, speaker, name, text in self._previewRows:
            name.setVisible(self.targetCheck.isChecked())

    def _key_changed(self, *args):
        self._refresh_key_state()
        self._sync_actions()

    def _refresh_key_state(self):
        state = "临时使用" if self.temporary_key() else (
            "已保存状态将在分析或打开设置时检测" if self._saved_key_present is None
            else "已保存" if self._saved_key_present else "未设置"
        )
        self.keyState.setText(state)
        self.forgetKeyButton.setEnabled(bool(self._saved_key_present and not self._busy))

    def _save(self):
        if not self._relationship():
            self._settings_feedback("请填写关系背景，或选择一个已有选项。", error=True)
            self.relEdit.setFocus()
            return
        try:
            preferences = settings.Preferences(
                relationship=self._relationship(),
                context=self._compatibility_context,
                reply_target=self.targetCheck.isChecked(),
                style=self.styleEdit.text().strip(),
            )
            settings.save_preferences(preferences)
        except (OSError, ValueError):
            self._settings_feedback("保存失败，请检查配置文件是否可写后重试。", error=True)
            return
        if self.rememberCheck.isChecked():
            key = self.temporary_key()
            if not key:
                self._settings_feedback("请先填写 Key，再选择保存在这台电脑上。", error=True)
                self.keyEdit.setFocus()
                return
            if self._saved_key_present is not False:
                warning = QMessageBox(self.win)
                warning.setWindowTitle("替换已保存 Key")
                warning.setText("这会替换 Windows 凭据管理器中现有的 Key。继续吗？")
                warning.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
                warning.setDefaultButton(QMessageBox.Cancel)
                warning.button(QMessageBox.Yes).setText("确认替换")
                warning.button(QMessageBox.Cancel).setText("取消")
                if warning.exec() != QMessageBox.Yes:
                    return
            try:
                if not self.on_save_key(key):
                    raise RuntimeError("save rejected")
            except Exception:
                self._settings_feedback("Key 保存失败；仍可临时使用，本次没有写入明文文件。", error=True)
                return
            self.clear_key_fields(saved_key_present=True)
            self._settings_feedback("偏好和 Key 已保存。")
            self._back_home()
            return
        self._settings_feedback("偏好已保存；Key 的保存状态未改变。")
        self._back_home()

    def _forget_key(self):
        if self._busy or not self._saved_key_present:
            return
        warning = QMessageBox(self.win)
        warning.setWindowTitle("忘记已保存 Key")
        warning.setText("这会从 Windows 凭据管理器删除已保存的 Key。")
        warning.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        warning.setDefaultButton(QMessageBox.Cancel)
        warning.button(QMessageBox.Yes).setText("确认忘记")
        warning.button(QMessageBox.Cancel).setText("取消")
        if warning.exec() != QMessageBox.Yes:
            return
        try:
            self.on_forget_key()
        except Exception:
            self._settings_feedback("未能删除已保存 Key，请稍后手动重试。", error=True)
            return
        self._settings_feedback("已忘记保存的 Key。")

    def _settings_feedback(self, text, error=False):
        color = PALETTE.error if error else _GREEN
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(self.settingsFeedback, qss, qss)
        self.settingsFeedback.setText(text)
        self.settingsFeedback.show()

    def open_settings(self):
        if self._busy:
            return
        try:
            self._saved_key_present = bool(self.on_key_status())
        except Exception:
            self._saved_key_present = None
            self._settings_feedback("无法检测已保存的 Key；可临时填写后分析。", error=True)
        self._refresh_key_state()
        self._sync_actions()
        self.pages.setCurrentWidget(self.settingsPage)
        self.workspaceButton.setChecked(False)
        self.settingsButton.setChecked(True)
        self.navDemoButton.setChecked(False)
        self.settingsButton.setEnabled(False)
        self.keyEdit.setFocus()

    def _back_home(self):
        self.pages.setCurrentWidget(self.home)
        self.workspaceButton.setChecked(not self._demo)
        self.navDemoButton.setChecked(self._demo)
        self.settingsButton.setChecked(False)
        self.settingsButton.setEnabled(not self._busy)
        self._sync_actions()

    def set_preview(self, model: PreviewModel, read_at=None) -> None:
        self.preview = model
        self.replyGoalEdit.clear()
        self.chatTitleLabel.setText(model.title or "未确认会话")
        self.chatTitleLabel.setToolTip(model.title)
        self.readTimeLabel.setText(
            "尚未读取" if read_at is None else read_at.astimezone().strftime("%H:%M 已读取")
        )
        self.targetEdit.setText("")
        self._render_preview()
        self._clear_result()
        self.set_busy(False)
        self._back_home()
        self.set_status("确认下方文字，再生成回复")

    def _render_preview(self):
        self.titleEdit.blockSignals(True)
        self.titleEdit.setText(self.preview.title)
        self.titleEdit.blockSignals(False)
        self._clear_preview_rows()
        for index, line in enumerate(self.preview.lines):
            row = _Surface()
            layout = QVBoxLayout(row)
            layout.setContentsMargins(10, 8, 10, 8)
            header = QHBoxLayout()
            speaker = ComboBox()
            speaker.addItems(["对方", "我"])
            speaker.setCurrentIndex(0 if line.who == "her" else 1)
            speaker.setAccessibleName(f"第 {index + 1} 条说话人")
            speaker.currentIndexChanged.connect(
                lambda value, i=index: self._speaker_changed(i, value)
            )
            header.addWidget(speaker)
            name = LineEdit()
            name.setText(line.name or "")
            name.setPlaceholderText("群聊发言人（单聊留空）")
            name.setAccessibleName(f"第 {index + 1} 条发言人名称")
            name.textChanged.connect(lambda value, i=index: self._name_changed(i, value))
            header.addWidget(name, 1)
            name.setVisible(self.targetCheck.isChecked())
            delete = PushButton("删除")
            delete.setFixedWidth(56)
            delete.setAccessibleName(f"删除第 {index + 1} 条消息")
            delete.clicked.connect(lambda checked=False, i=index: self._delete_line(i))
            header.addWidget(delete)
            layout.addLayout(header)
            text = PlainTextEdit()
            text.setPlainText(line.text)
            text.setFixedHeight(70)
            text.setAccessibleName(f"第 {index + 1} 条消息正文")
            text.textChanged.connect(lambda i=index, edit=text: self._text_changed(i, edit.toPlainText()))
            layout.addWidget(text)
            self.previewBox.addWidget(row)
            self._name_controls(row)
            self._previewRows.append((row, speaker, name, text))
        self._sync_actions()
        self._sync_target_row()
        self._update_preview_context()
        self._update_analysis_guidance()

    def _clear_preview_rows(self):
        for row, speaker, name, text in self._previewRows:
            speaker.blockSignals(True)
            name.blockSignals(True)
            text.blockSignals(True)
            name.setText("")
            text.clear()
            text.document().clearUndoRedoStacks()
            self.previewBox.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._previewRows = []

    def _title_changed(self, title):
        self.preview.title = title
        self._preview_changed()

    def _text_changed(self, index, text):
        self.preview.set_text(index, text)
        self._preview_changed()

    def _speaker_changed(self, index, value):
        self.preview.set_speaker(index, "her" if value == 0 else "me")
        self._preview_changed()

    def _name_changed(self, index, name):
        self.preview.set_name(index, name)
        self._preview_changed()

    def _delete_line(self, index):
        self.preview.delete(index)
        self._render_preview()
        self._preview_changed()

    def _append_line(self):
        self.preview.append(ChatLine("her", ""))
        self._render_preview()
        self._preview_changed()

    def _redact(self):
        self.preview.apply_redaction()
        self._render_preview()
        self._preview_changed()

    def _undo_redaction(self):
        self.preview.undo_redaction()
        self._render_preview()
        self._preview_changed()

    def _preview_changed(self, *args):
        self._clear_result()
        self._sync_actions()
        self._sync_target_row()
        self._update_preview_context()
        self._update_analysis_guidance()

    def _confirmed_preview_lines(self) -> tuple[ChatLine, ...]:
        return tuple(line for line in self.preview.lines if str(line.text).strip())

    def _analysis_mode(self) -> str:
        lines = self._confirmed_preview_lines()
        return "follow_up" if lines and lines[-1].who == "me" else "reply"

    def _update_analysis_guidance(self) -> None:
        follow_up = self._analysis_mode() == "follow_up"
        self.modeHint.setText(
            "最后一句是你发出的，将按主动跟进模式分析"
            if follow_up else "将按回复模式分析"
        )
        self.replyGoalEdit.setPlaceholderText(
            "例如：继续聊演唱会，顺便问他周末有没有空"
            if follow_up else "例如：先肯定对方，再问清楚时间"
        )

    def set_analysis_source(self, source: str) -> None:
        if source not in ("live_verified", "user_confirmed_only"):
            raise ValueError("invalid analysis source")
        self._analysis_source = source
        text = (
            "来源：微信可见文字 · 可填入"
            if source == "live_verified"
            else "来源：手动整理 · 仅复制"
        )
        self.sourceHint.setText(text)

    def _is_group_chat(self) -> bool:
        return any(
            line.name is not None and str(line.name).strip()
            for line in self._confirmed_preview_lines()
        )

    def _sync_target_row(self) -> None:
        self.targetRow.setVisible(bool(self.targetCheck.isChecked() and self._is_group_chat()))

    def _preview_context_text(self, limit: int | None = None) -> str:
        def display(value: str) -> str:
            return value[:limit] + "…" if limit is not None and len(value) > limit else value

        relationship = next((name for name, value in _RELATIONSHIPS if value == self._relationship()), self._relationship()) or "未指定"
        style = self.styleEdit.text().strip() or "未指定"
        confirmed_lines = self._confirmed_preview_lines()
        is_group = any(
            line.name is not None and str(line.name).strip()
            for line in confirmed_lines
        )
        parts = [
            f"关系：{display(relationship)}",
            f"口吻：{display(style)}",
            f"可见消息：{len(confirmed_lines)} 条",
            "群聊" if is_group else "单聊",
        ]
        target = self.targetEdit.text().strip() if self.targetCheck.isChecked() else ""
        if is_group and target:
            parts.append(f"回复对象：{display(target)}")
        return "｜".join(parts)

    def _update_preview_context(self) -> None:
        full = self._preview_context_text()
        self.previewContext.setText(self._preview_context_text(limit=12))
        self.previewContext.setToolTip(full)
        self.previewContext.setAccessibleDescription(full)
        self.chatTitleLabel.setToolTip(full)
        self.chatTitleLabel.setAccessibleDescription(full)

    def confirmed_snapshot(self) -> ConfirmedSnapshot:
        confirmed_lines = self._confirmed_preview_lines()
        reply_to = (
            self.targetEdit.text()
            if self.targetCheck.isChecked() and self._is_group_chat()
            else None
        )
        return self.preview.snapshot(
            self._relationship(), len(confirmed_lines), self.styleEdit.text(), reply_to,
            self.replyGoalEdit.text(),
        )

    def temporary_key(self) -> str:
        return self.keyEdit.text().strip()

    def _has_key(self) -> bool:
        return bool(self.temporary_key() or self._saved_key_present is not False)

    def _sync_actions(self):
        has_text = any(line.text.strip() for line in self.preview.lines)
        self.analyzeButton.setEnabled(
            not self._busy and not self._demo and has_text and self._has_key() and bool(self._relationship())
        )
        self.readButton.setEnabled(not self._busy)
        if hasattr(self, "clearButton"):
            self.clearButton.setVisible(bool(self.preview.lines or self.cands))
        self.previewPanel.setEnabled(not self._busy)
        self.replyGoalEdit.setEnabled(not self._busy)
        self.welcomePanel.setVisible(not self.preview.lines and not self.cands)
        self.welcomeSpace.setVisible(not self.preview.lines and not self.cands)
        if hasattr(self, "welcomeBottomSpace"):
            self.welcomeBottomSpace.setVisible(not self.preview.lines and not self.cands)
            self.welcomeReadButton.setEnabled(not self._busy)
        self.focusPages.setVisible(bool(self.preview.lines or self.cands))
        self.previewPanel.setVisible(bool(self.preview.lines))
        self.focusTabs.setVisible(self._compact and bool(self.preview.lines or self.cands))
        if hasattr(self, "connectionLabel"):
            if self.temporary_key():
                key_status = "临时 Key 已设置"
            elif self._saved_key_present is True:
                key_status = "Key 已保存"
            elif self._saved_key_present is None:
                key_status = "Key 状态待确认"
            else:
                key_status = "Key 未设置"
            self.connectionLabel.setText(key_status)
        self._sync_editor()
        self.setupButton.setVisible(not self._has_key())
        self.forgetKeyButton.setEnabled(bool(self._saved_key_present and not self._busy))
        for card in self.cards:
            card.set_available(self._current and not self._busy)
            if self._fill_consumed:
                card.set_fill_available(False)

    def _read(self):
        if not self._busy:
            if self._demo:
                self.set_preview(PreviewModel("", []))
            self._demo = False
            self.set_analysis_source("user_confirmed_only")
            try:
                self.on_read()
            except Exception:
                self.show_stage_error("读取", "读取失败，请检查微信窗口后手动重试。")

    def _analyze(self):
        if not self.analyzeButton.isEnabled():
            return
        try:
            snapshot = self.confirmed_snapshot()
        except (ValueError, TypeError):
            self.set_status("请检查预览内容和关系背景后再开始分析。", "warning")
            return
        try:
            self.on_analyze(snapshot)
        except Exception:
            self.show_stage_error("分析", "分析未完成，请检查输入后手动重试。")

    def _fill(self, index):
        reply = self._reply_views.get(index)
        if (self._busy or not self._current or self._fill_consumed
                or reply is None or not reply.fill_allowed):
            return
        try:
            self.on_fill(self._drafts.get(index, reply.text))
        except Exception:
            self.show_fill_error("请确认微信窗口可用后重试，或复制回复。")

    def _copy(self, index):
        reply = self._reply_views.get(index)
        if self._busy or not self._current or reply is None or not reply.copy_allowed:
            return
        self.app.clipboard().setText(self._drafts.get(index, reply.text))
        self.set_status("回复已复制，可在微信中粘贴并修改。", "success")

    def _clear(self):
        self.on_clear()

    def clear_chat_fields(self, saved_key_present=None) -> None:
        self._demo = False
        if saved_key_present is not None:
            self._saved_key_present = bool(saved_key_present)
        self.preview = PreviewModel("", [])
        self._clear_preview_rows()
        # setText also drops the line edits' undo histories.
        self.titleEdit.setText("")
        self.replyGoalEdit.clear()
        self.targetEdit.setText("")
        self.set_analysis_source("user_confirmed_only")
        self.keyEdit.setText("")
        self.keyEdit.setEchoMode(QLineEdit.Password)
        self.rememberCheck.setChecked(False)
        self._load_settings()
        self.settingsFeedback.clear()
        self.settingsFeedback.hide()
        self._clear_result()
        self._select_home_view(0)
        self.chatTitleLabel.setText("未确认会话")
        self.readTimeLabel.setText("尚未读取")
        self._refresh_key_state()
        self.set_busy(False)
        self._back_home()
        self._update_preview_context()
        self._update_analysis_guidance()
        self.set_status("本次内容已清除；点击“读取当前聊天”重新开始")

    def clear_key_fields(self, saved_key_present=False) -> None:
        self._saved_key_present = bool(saved_key_present)
        self.keyEdit.setText("")
        self.keyEdit.setEchoMode(QLineEdit.Password)
        self.rememberCheck.setChecked(False)
        self._refresh_key_state()
        self._sync_actions()

    def _clear_result(self):
        self._rewrite_context_available = False
        self._clear_editor()
        self.cands = []
        self._reply_views = {}
        self._current = False
        self._fill_consumed = False
        self._clear_cards()
        self.timeline.clear()
        self.centerPages.setCurrentWidget(self.previewScroll)
        self.replyStatus.setText("未生成回复")
        self.actionLabel.clear()
        self.reasonLabel.clear()
        self.reasonLabel.hide()
        self.fallbackLabel.clear()
        self.fallbackLabel.hide()
        self.unusedToggle.setChecked(False)
        self.unusedToggle.hide()
        self.primaryReplyContent.hide()
        self.otherReplyContent.hide()
        self.updated.clear()
        self.empty.show()
        self._empty_text()
        self._update_reply_tab(0)
        self._select_home_view(0)

    def set_busy(self, busy):
        self._busy = bool(busy)
        self.progress.setVisible(self._busy)
        self.settingsPage.setEnabled(not self._busy)
        self.settingsButton.setEnabled(not self._busy and self.pages.currentWidget() != self.settingsPage)
        if self._busy:
            self.invalidate_replies()
            self.progress.start()
        else:
            self.progress.stop()
        self._sync_actions()

    def _empty_text(self):
        self.emptyTitle.setText("读取后确认文本，再开始分析")
        self.emptyHint.setText("也可以添加消息，手动整理本次预览。")

    def invalidate_replies(self):
        self._rewrite_context_available = False
        self._current = False
        for card in self.cards:
            card.set_available(False)
        self._sync_editor()

    def set_status(self, text, kind="idle"):
        colors = {
            "idle": PALETTE.muted,
            "busy": PALETTE.primary,
            "success": PALETTE.primary,
            "warning": PALETTE.warning,
            "error": PALETTE.error,
        }
        markers = {"idle": "·", "busy": "·", "success": "·", "warning": "!", "error": "!"}
        qss = f"BodyLabel {{ color: {colors.get(kind, _MUTED)}; background: transparent; }}"
        setCustomStyleSheet(self.status, qss, qss)
        self.status.setText(f"{markers.get(kind, '●')}  {text}")
        self.status.setVisible(kind in ("busy", "success", "warning", "error"))
        self.headerPanel.setToolTip(text)

    def show_stage_error(self, stage: str, message: str, candidates: list[str] | None = None) -> None:
        """Accept stage-safe advice; never render raw request/response bodies."""
        retained = list(candidates or [])
        self.set_busy(False)
        self._clear_result()
        self.render_result_view(project_unreviewed_candidates(retained))
        if stage == "Jev 判断" and retained:
            self.replyStatus.setText(f"生成 {len(retained)} 条，Jev 判断未完成，候选仅供复制")
        elif stage != "Jev 判断":
            self.actionLabel.clear()
            if retained:
                self.replyStatus.setText(f"{stage}未完成，候选仅供复制")
        self.empty.setVisible(not retained)
        if not retained:
            self.emptyTitle.setText(f"{stage}未完成")
            self.emptyHint.setText("请按上方提示处理，然后手动重试。")
        key = self.temporary_key()
        safe_message = str(message).replace(key, "[已隐藏]") if key else str(message)
        self.set_status(f"{stage}未完成：{safe_message}", "error")
        self._select_home_view(1 if retained else 0)
        self._sync_actions()

    def show(self, result, *, allow_fill: bool = True):
        """Render typed analysis without inferring any candidate eligibility."""
        self._clear_result()
        self.set_busy(False)
        self._rewrite_context_available = True
        view = project_result(result, allow_fill=allow_fill)
        lines = tuple(ChatLine(row["from"], row["text"], row.get("name"))
                      for row in result.messages)
        self.timeline.set_conversation(lines, view)
        self.centerPages.setCurrentWidget(self.timeline)
        self.render_result_view(view)
        self.updated.setText(datetime.now().strftime("%H:%M 本次分析"))
        self.set_status(view.status_text, "success")
        # Ordinary success is already in the workspace; retain safety caveats.
        self.status.setVisible(view.reply_state != "reply" or result.mode == "follow_up")
        self._select_home_view(1)

    def mark_fill_consumed(self) -> None:
        self._fill_consumed = True
        for card in self.cards:
            card.set_fill_available(False)
        self._sync_editor()

    def show_fill_error(self, message: str, candidates: list[str] | None = None) -> None:
        self.set_busy(False)
        self.set_status(f"填入未完成：{message}", "error")
        self._sync_actions()

    def render_result_view(self, view: ResultView):
        self._clear_cards()
        self._clear_editor()
        self._reply_state = view.reply_state
        self._update_reply_tab(len(view.ordered_replies))
        self._reply_views = {row.source_index: row for row in view.ordered_replies}
        self.cands = [row.text for row in sorted(view.ordered_replies, key=lambda row: row.source_index)]
        self._current = True
        self.replyStatus.setText(view.heading_text)
        self.actionLabel.setText(view.action_text)
        self.actionLabel.setVisible(bool(view.action_text))
        self.reasonLabel.setText("\n".join(view.reason_lines))
        self.reasonLabel.setVisible(bool(view.reason_lines))
        self.fallbackLabel.setText("如果你仍想礼貌回应")
        self.fallbackLabel.setVisible(any(row.role == "fallback" for row in view.ordered_replies))
        primary = [row for row in view.ordered_replies if row.role in ("recommended", "fallback")]
        if view.reply_state in ("unsafe", "unreviewed"):
            primary = []
        self._other_toggle_open_text = view.toggle_text or "查看其他备选"
        self._other_toggle_close_text = self._other_toggle_open_text.replace("查看", "收起", 1)
        self.unusedToggle.setChecked(False)
        self._set_other_replies_visible(False)
        self.unusedToggle.setVisible(False)
        for position, reply in enumerate(view.ordered_replies, 1):
            card = _ReplyCard(self, reply, number=position)
            self.otherReplyBox.addWidget(card)
            self.cards.append(card)
        self.primaryReplyContent.hide()
        self.otherReplyContent.setVisible(False)
        self.empty.hide()
        if view.ordered_replies:
            self.select_reply(view.ordered_replies[0].source_index)
        self._set_other_replies_visible(True)
        self._sync_actions()

    def _set_other_replies_visible(self, checked: bool) -> None:
        visible = len(self._reply_views) > 1
        self.otherReplyContent.setVisible(visible)
        if hasattr(self, "replyLayout"):
            inset = 12 if self._compact else 16
            self.replyLayout.setContentsMargins(inset, inset, inset, inset)
            self.replyLayout.setSpacing(8 if self._compact else 12)
            self.editorPanel.layout().setSpacing(8 if self._compact else 12)
            self.draftEdit.setMinimumHeight(96 if self._compact and (visible or self.refineToggle.isChecked()) else 180)
            self.replyStatus.setVisible(getattr(self, "_reply_state", None) != "reply")
            self.actionLabel.setVisible(bool(self.actionLabel.text()))
            self.replyLayout.setStretchFactor(self.editorPanel, 1 if visible else 3)
            self.replyLayout.setStretchFactor(self.replyScroll, 2 if visible else 1)
        text = self._other_toggle_open_text
        self.unusedToggle.setText(text)
        self.unusedToggle.setAccessibleName(text)

    def show_waiting_for_reply(self, message="已读取到你刚发出的消息，等待对方回复。"):
        self._clear_result()
        self.cands = []
        self._reply_views = {}
        self.set_busy(False)
        self.empty.hide()
        self.replyStatus.setText(message)
        self.set_status(message)
        self.status.show()
        self.replyStatus.show()
        self._select_home_view(0)

    def show_stale_chat(self, message="聊天已有变化，请重新读取后分析。"):
        selected = self._selected_index
        consumed = self._fill_consumed
        retained = tuple(self._drafts.get(index, text) for index, text in enumerate(self.cands))
        self._clear_result()
        self._fill_consumed = consumed
        self.set_busy(False)
        self.render_result_view(project_unreviewed_candidates(retained))
        if selected is not None:
            self.select_reply(selected)
        self.replyStatus.setText(message)
        self.actionLabel.clear()
        self.set_status(message, "warning")
        self._select_home_view(1 if retained else 0)

    def _clear_cards(self):
        for card in self.cards:
            card.text.clear()
            self.primaryReplyBox.removeWidget(card)
            self.otherReplyBox.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = []

    def after(self, ms, fn):
        QTimer.singleShot(ms, fn)

    def run(self):
        self.app.exec()
