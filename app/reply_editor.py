"""Selected draft workspace; eligibility always belongs to the original review."""
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, LineEdit, PlainTextEdit, PrimaryPushButton, PushButton
from qfluentwidgets import setFont
from app.ui_theme import PALETTE


class ReplyEditorMixin:
    def build_reply_editor(self, layout):
        self.editorPanel = QWidget()
        self.editorPanel.setObjectName("selectedDraft")
        self.editorPanel.setStyleSheet(f"QWidget#selectedDraft {{background: {PALETTE.surface};}}")
        box = QVBoxLayout(self.editorPanel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        self.draftBadge = BodyLabel()
        box.addWidget(self.draftBadge)
        self.draftEdit = PlainTextEdit()
        self.draftEdit.setAccessibleName("选中的回复，可直接修改")
        self.draftEdit.setMinimumHeight(180)
        setFont(self.draftEdit, 15)
        self.draftEdit.textChanged.connect(self._draft_changed)
        box.addWidget(self.draftEdit, 1)
        actions = QHBoxLayout()
        self.copyDraftButton = PushButton("复制回复")
        self.copyDraftButton.clicked.connect(lambda: self._copy(self._selected_index))
        self.fillDraftButton = PrimaryPushButton("填入微信（追加）")
        self.fillDraftButton.setToolTip("追加到微信输入框；检查后由你手动发送。")
        self.fillDraftButton.setAccessibleName("追加选中的回复到微信输入框")
        self.fillDraftButton.clicked.connect(lambda: self._fill(self._selected_index))
        actions.addWidget(self.copyDraftButton)
        actions.addWidget(self.fillDraftButton)
        self.refineToggle = PushButton("调整语气与表达")
        self.refineToggle.setStyleSheet(f"QPushButton {{border: none; background: transparent; color: {PALETTE.muted}; padding: 4px; text-align: left;}} QPushButton:hover {{background: {PALETTE.candidate};}}")
        self.refineToggle.setCheckable(True)
        box.addWidget(self.refineToggle)
        self.refinePanel = QWidget()
        refinement = QVBoxLayout(self.refinePanel)
        refinement.setContentsMargins(0, 0, 0, 0)
        self.refineToggle.toggled.connect(self.refinePanel.setVisible)
        self.refineToggle.toggled.connect(lambda: self._set_other_replies_visible(self.unusedToggle.isChecked()))
        quick = QHBoxLayout()
        self.refineButtons = []
        for hint in ("更简短", "更自然", "更直接"):
            button = PushButton(hint)
            button.clicked.connect(lambda checked=False, value=hint: self._rewrite(value))
            quick.addWidget(button)
            self.refineButtons.append(button)
        refinement.addLayout(quick)
        self.refineEdit = LineEdit()
        self.refineEdit.setMaxLength(500)
        self.refineEdit.setPlaceholderText("怎么改？例如：不用反问（可选）")
        self.refineEdit.setAccessibleName("改写要求")
        refinement.addWidget(self.refineEdit)
        self.rewriteButton = PushButton("按要求改写")
        self.rewriteButton.clicked.connect(lambda: self._rewrite(self.refineEdit.text()))
        refinement.addWidget(self.rewriteButton)
        self.refinePanel.setMinimumHeight(refinement.sizeHint().height())
        box.addWidget(self.refinePanel)
        self.refinePanel.hide()
        box.addLayout(actions)
        layout.addWidget(self.editorPanel, 3)
        self._selected_index = None
        self._rewrite_context_available = False
        self._drafts = {}
        self.editorPanel.hide()

    def select_reply(self, index):
        if self._busy or index not in self._reply_views:
            return
        self._selected_index = index
        reply = self._reply_views[index]
        self.draftEdit.setPlainText(self._drafts.get(index, reply.text))
        self.editorPanel.show()
        self._draft_changed()
        self._sync_editor()
        # The editor is the selected candidate; show only actual alternatives.
        for card in self.cards:
            card.setVisible(card.reply.source_index != index)
        self._set_other_replies_visible(True)

    def _draft_changed(self):
        reply = self._reply_views.get(self._selected_index)
        if reply is None:
            return
        text = self.draftEdit.toPlainText()
        self._drafts[self._selected_index] = text
        for card in self.cards:
            if card.reply.source_index == self._selected_index:
                edited = text != reply.text
                card.text.setText(text)
                card.scoreBadge.setVisible(not edited and reply.percent_text is not None)
                card.roleLabel.setText("你的修改 · 未重新评分" if edited else card.originalLabel)
        self.draftBadge.setText(
            "你的修改 · 未重新评分" if text != reply.text
            else ("离线示例" if self._demo else "Jev 建议" if reply.safe_to_send else "仅供参考")
            + (f" · {reply.percent_text}" if reply.percent_text else " · 未完成判断")
        )
        self._sync_editor()

    def _sync_editor(self):
        if not hasattr(self, "draftEdit"):
            return
        reply = self._reply_views.get(self._selected_index)
        active = bool(reply and self._current and not self._busy)
        has_text = bool(self.draftEdit.toPlainText().strip())
        self.draftEdit.setReadOnly(not active)
        self.copyDraftButton.setEnabled(active and has_text)
        self.fillDraftButton.setEnabled(bool(active and has_text and reply.fill_allowed and not self._fill_consumed))
        self.fillDraftButton.setVisible(bool(reply and reply.fill_allowed))
        self.fillDraftButton.setText("仍要填入微信（追加）" if getattr(self, "_reply_state", None) == "pause" else "填入微信（追加）")
        for button in [*self.refineButtons, self.rewriteButton]:
            button.setEnabled(active and has_text and self.on_rewrite is not None
                              and self._rewrite_context_available and not self._demo)
        self.refineEdit.setEnabled(active and self._rewrite_context_available and not self._demo)

    def _rewrite(self, hint):
        if (self._busy or not self._current or self._demo or not self.on_rewrite
                or not self._rewrite_context_available):
            return
        hint = hint.strip()
        if not hint or not self.draftEdit.toPlainText().strip():
            return
        self.on_rewrite(self.draftEdit.toPlainText(), hint)

    def set_rewrite_busy(self):
        self._busy = True
        self.progress.show()
        self.progress.start()
        self._sync_actions()

    def show_rewrite_error(self, message):
        self.set_busy(False)
        self.set_status("改写未完成；保留上一份草稿。" + message, "warning")

    def _clear_editor(self):
        self._selected_index = None
        self._drafts = {}
        self.draftEdit.clear()
        self.draftEdit.document().clearUndoRedoStacks()
        self.refineEdit.clear()
        self.draftBadge.clear()
        self.editorPanel.hide()

    def show_demo(self):
        from app.demo import demo_result
        from app.preview_model import PreviewModel
        from app.privacy import ChatLine
        if self._busy:
            return
        self.on_clear()
        self._demo = True
        result = demo_result()
        self.set_analysis_source("user_confirmed_only")
        self.set_preview(PreviewModel("离线示例 · 周末安排", [ChatLine(row["from"], row["text"]) for row in result.messages]))
        self.show(result, allow_fill=False)
        self.sourceHint.setText("离线示例 · 固定演示内容，不会发送到云端，也不能填入微信。")
        self.set_status("离线示例 · 可编辑体验；读取自己的聊天即可退出")
        self._sync_actions()
