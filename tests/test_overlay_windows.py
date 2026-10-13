"""Real Windows Qt checks for interaction affordances that doubles cannot prove."""

from __future__ import annotations

import os
import math
import sys
import unittest
from unittest.mock import patch

from tests.test_result_view import _result


@unittest.skipUnless(sys.platform == "win32", "requires the Windows Qt runtime")
class WindowsOverlayInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QAbstractButton, QLabel

        from app import settings
        from app.overlay import Overlay

        cls.Qt = Qt
        cls.QTest = QTest
        cls.QAbstractButton = QAbstractButton
        cls.QLabel = QLabel
        cls.settings = settings
        cls.Overlay = Overlay

    def setUp(self):
        self.preferences = patch.object(
            self.settings, "load_preferences", return_value=self.settings.Preferences()
        )
        self.migration = patch.object(
            self.settings, "migrate_legacy_preferences", return_value=False
        )
        self.preferences.start()
        self.migration.start()
        self.ui = self.Overlay(
            lambda: None,
            lambda *args: None,
            lambda *args: None,
            lambda: None,
            lambda *args: False,
            lambda: None,
            lambda: False,
        )
        self.ui.app.processEvents()

    def tearDown(self):
        self.ui.win.close()
        self.ui.app.processEvents()
        self.migration.stop()
        self.preferences.stop()

    def test_edited_selected_reply_copies_and_hides_original_score(self):
        self.ui.show(_result())
        self.ui.draftEdit.setPlainText("这是我的修改")
        self.ui.copyDraftButton.click()
        self.assertEqual(self.ui.app.clipboard().text(), "这是我的修改")
        self.assertNotIn("%", self.ui.draftBadge.text())
        self.assertIn("你的修改", self.ui.draftBadge.text())

    def test_selection_keeps_single_fill_and_edit_cannot_upgrade_copy_only(self):
        self.ui.show(_result(), allow_fill=False)
        self.ui.draftEdit.setPlainText("修改后")
        self.assertFalse(self.ui.fillDraftButton.isEnabled())
        self.ui.show(_result())
        self.ui.select_reply(1)
        self.assertTrue(self.ui.fillDraftButton.isEnabled())
        self.ui.mark_fill_consumed()
        self.ui.select_reply(0)
        self.assertFalse(self.ui.fillDraftButton.isEnabled())

    def test_rewrite_busy_preserves_edit_and_clear_drops_all_draft_text(self):
        self.ui.show(_result())
        self.ui.draftEdit.setPlainText("保留的修改")
        self.ui.refineEdit.setText("不要问问题")
        self.ui.set_rewrite_busy()
        self.assertEqual(self.ui.draftEdit.toPlainText(), "保留的修改")
        self.assertFalse(self.ui.copyDraftButton.isEnabled())
        self.ui.show_rewrite_error("请手动重试")
        self.assertEqual(self.ui.draftEdit.toPlainText(), "保留的修改")
        self.assertTrue(self.ui.copyDraftButton.isEnabled())
        self.ui.clear_chat_fields()
        self.assertEqual(self.ui.draftEdit.toPlainText(), "")
        self.assertEqual(self.ui.refineEdit.text(), "")

    def test_demo_never_analyzes_or_fills_and_generate_stays_outside_scroll(self):
        self.ui.show_demo()
        self.assertFalse(self.ui.fillDraftButton.isEnabled())
        self.assertFalse(self.ui.analyzeButton.isEnabled())
        self.assertIn("示例", self.ui.sourceHint.text())
        self.assertFalse(self.ui.previewScroll.isAncestorOf(self.ui.analyzeButton))

    def test_demo_read_failure_does_not_leave_demo_input_eligible_for_cloud(self):
        self.ui._saved_key_present = True
        self.ui.show_demo()
        self.ui.on_read = lambda: (_ for _ in ()).throw(RuntimeError("synthetic capture failure"))
        self.ui._read()
        self.assertFalse(self.ui.analyzeButton.isEnabled())
        self.assertFalse(self.ui.preview.lines)
        self.assertNotIn("示例", self.ui.sourceHint.text())

    def test_refine_click_uses_selected_edit_and_disables_concurrent_action(self):
        requests = []
        def rewrite(text, hint):
            requests.append((text, hint))
            self.ui.set_rewrite_busy()
        self.ui.on_rewrite = rewrite
        self.ui.show(_result())
        self.ui.select_reply(0)
        self.ui.draftEdit.setPlainText("选中的修改")
        self.ui.refineButtons[0].click()
        self.ui.refineButtons[0].click()
        self.assertEqual(requests, [("选中的修改", "更简短")])

    def test_unsafe_edit_never_becomes_fillable(self):
        self.ui.show(_result(candidate_safety_probabilities=(0.1, 0.2), best_index=None))
        self.ui.draftEdit.setPlainText("看起来正常的文字")
        self.assertFalse(self.ui.fillDraftButton.isEnabled())

    def test_stale_chat_preserves_selected_edited_draft_only_for_copy(self):
        requests = []
        self.ui.on_rewrite = lambda text, hint: requests.append((text, hint))
        self.ui.show(_result())
        self.ui.draftEdit.setPlainText("过期前的修改")
        self.ui.mark_fill_consumed()
        self.ui.show_stale_chat()
        self.assertEqual(self.ui.draftEdit.toPlainText(), "过期前的修改")
        self.assertFalse(self.ui.fillDraftButton.isEnabled())
        self.assertTrue(self.ui.copyDraftButton.isEnabled())
        self.assertTrue(all(not button.isEnabled() for button in self.ui.refineButtons))
        self.assertFalse(self.ui.rewriteButton.isEnabled())
        self.assertFalse(self.ui.refineEdit.isEnabled())
        self.assertTrue(self.ui._fill_consumed)
        self.ui._rewrite("更自然")
        self.assertEqual(requests, [])

    def test_valid_manual_only_unreviewed_context_can_refine(self):
        requests = []
        self.ui.on_rewrite = lambda text, hint: requests.append((text, hint))
        self.ui.set_analysis_source("user_confirmed_only")
        self.ui.show(_result(reply_scores=()), allow_fill=False)
        self.ui.draftEdit.setPlainText("手工内容的修改")
        self.ui.refineEdit.setText("不反问")
        self.ui.rewriteButton.click()
        self.assertEqual(requests, [("手工内容的修改", "不反问")])
        self.assertFalse(self.ui.fillDraftButton.isEnabled())

    def test_native_window_resizes_without_custom_grip(self):
        self.assertFalse(self.ui.win.windowFlags() & self.Qt.FramelessWindowHint)
        self.assertTrue(self.ui.win.windowFlags() & self.Qt.WindowMaximizeButtonHint)
        self.ui.win.resize(1120, 800)
        self.assertEqual(self.ui.win.width(), 1120)

    def test_five_replies_use_one_recommended_and_four_grouped_candidates(self):
        result = _result(
            candidates=("一", "二", "三", "四", "五"),
            reply_scores=(0.10, 0.20, 0.50, 0.15, 0.05),
            candidate_safety_probabilities=(0.8,) * 5,
            best_index=2,
        )
        self.ui.show(result)
        self.ui.app.processEvents()

        self.assertEqual(len(self.ui.cards), 5)
        self.assertEqual([card.objectName() for card in self.ui.cards].count("recommendedReply"), 1)
        self.assertEqual([card.objectName() for card in self.ui.cards].count("candidateReply"), 4)
        self.assertEqual(sum(card.fillButton.isVisible() for card in self.ui.cards), 0)
        self.assertTrue(self.ui.fillDraftButton.isVisible())
        self.assertEqual(self.ui.cards[0].scoreBadge.text(), "50%")
        self.assertEqual([card.reply.source_index for card in self.ui.cards], [2, 1, 3, 0, 4])
        self.assertEqual(self.ui.replyTab.text(), "回复建议 5")
        from app.ui_theme import PALETTE

        recommended = next(card for card in self.ui.cards if card.reply.recommended)
        candidates = [card for card in self.ui.cards if not card.reply.recommended]
        self.assertIn(PALETTE.recommended.lower(), recommended.styleSheet().lower())
        self.assertTrue(all(PALETTE.candidate.lower() in card.styleSheet().lower()
                            for card in candidates))
        self.assertTrue(all("border-radius: 12px" in card.styleSheet()
                            for card in self.ui.cards))
        self.assertEqual(self.ui.primaryReplyBox.spacing(), 10)
        self.assertEqual(self.ui.otherReplyBox.spacing(), 10)

    def test_append_fill_control_explains_action_and_consumption_keeps_copy(self):
        self.ui.show(_result(), allow_fill=True)
        self.ui.app.processEvents()
        button = self.ui.fillDraftButton
        self.assertEqual(button.text(), "填入微信（追加）")
        self.assertEqual(
            button.toolTip(),
            "追加到微信输入框；检查后由你手动发送。",
        )
        self.assertEqual(button.accessibleName(), "追加选中的回复到微信输入框")
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        self.ui.mark_fill_consumed()
        self.ui._sync_actions()
        self.ui.app.processEvents()
        self.assertFalse(button.isEnabled())
        self.assertTrue(self.ui.copyDraftButton.isEnabled())

    def test_analysis_freezes_goal_until_success_or_failure(self):
        for outcome in ("success", "failure"):
            with self.subTest(outcome=outcome):
                self.ui.replyGoalEdit.setText("frozen goal")
                self.ui.set_busy(True)
                self.ui.app.processEvents()
                self.assertFalse(self.ui.replyGoalEdit.isEnabled())
                self.QTest.keyClicks(self.ui.replyGoalEdit, " changed")
                self.assertEqual(self.ui.replyGoalEdit.text(), "frozen goal")

                if outcome == "success":
                    self.ui.show(_result())
                else:
                    self.ui.show_stage_error("分析", "合成失败")
                self.ui._select_home_view(0)
                self.ui.app.processEvents()
                self.assertTrue(self.ui.replyGoalEdit.isEnabled())
                self.ui.replyGoalEdit.setFocus()
                self.ui.replyGoalEdit.selectAll()
                self.QTest.keyClicks(self.ui.replyGoalEdit, "new goal")
                self.assertEqual(self.ui.replyGoalEdit.text(), "new goal")

    def test_follow_up_hides_global_historical_action_and_reply_restores_it(self):
        from core.adaptive_analysis import normalize_messages

        self.ui.show(_result())
        self.ui.app.processEvents()
        self.assertTrue(self.ui.actionLabel.isVisible())
        self.assertEqual(self.ui.actionLabel.text(), "建议：商量具体安排")

        self.ui.show(_result(
            mode="follow_up",
            messages=normalize_messages([("her", "周末去哪"), ("me", "我想去公园")]),
        ))
        self.ui.app.processEvents()
        self.assertFalse(self.ui.actionLabel.isVisible())
        self.assertEqual(self.ui.actionLabel.text(), "")

        self.ui.show(_result())
        self.ui.app.processEvents()
        self.assertTrue(self.ui.actionLabel.isVisible())
        self.assertEqual(self.ui.actionLabel.text(), "建议：商量具体安排")

    def test_reply_body_uses_the_approved_text_color(self):
        from PySide6.QtGui import QPalette
        from app.ui_theme import PALETTE

        self.ui.show(_result(candidates=("正文颜色",), reply_scores=(1.0,),
                             candidate_safety_probabilities=(0.8,), best_index=0))
        self.ui.app.processEvents()
        body = self.ui.cards[0].text
        body.ensurePolished()

        self.assertEqual(
            body.palette().color(QPalette.WindowText).name(), PALETTE.text.lower()
        )

    def test_long_legal_context_keeps_preview_scroll_usable_at_supported_widths(self):
        self.ui._append_line()
        self.ui.relationshipBox.setCurrentIndex(self.ui.relationshipBox.count() - 1)
        self.ui.relEdit.setText("关" * 500)
        self.ui.styleEdit.setText("口" * 500)
        self.ui.app.processEvents()

        for width in (720, 820, 1120):
            with self.subTest(width=width):
                self.ui.win.resize(width, 820)
                self.ui.app.processEvents()
                self.assertFalse(self.ui.previewContext.isVisible())
                self.assertGreaterEqual(self.ui.previewScroll.viewport().height(), 180)
                self.ui.centerPages.setCurrentWidget(self.ui.timeline)
                self.ui.app.processEvents()
                self.assertGreaterEqual(self.ui.timeline.scroll.viewport().height(), 180)
                self.ui.centerPages.setCurrentWidget(self.ui.previewScroll)

        self.assertLess(len(self.ui.previewContext.text()), 200)
        self.assertIn("关" * 500, self.ui.chatTitleLabel.toolTip())
        self.assertIn("口" * 500, self.ui.chatTitleLabel.accessibleDescription())
        self.ui.open_settings()
        self.assertEqual(self.ui.relEdit.text(), "关" * 500)
        self.assertEqual(self.ui.styleEdit.text(), "口" * 500)

    def test_reply_card_semantic_backgrounds_are_painted_in_window_capture(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QImage

        result = _result(
            candidates=("推荐卡像素验证", "候选一", "候选二", "候选三", "候选四"),
            reply_scores=(0.10, 0.50, 0.20, 0.15, 0.05),
            candidate_safety_probabilities=(0.8,) * 5,
            best_index=1,
        )
        self.ui.show(result)
        self.ui.unusedToggle.click()
        self.ui.win.resize(820, 820)
        self.ui.app.processEvents()

        expected_colors = {
            "recommendedReply": "#EDF6F0",
            "candidateReply": "#F8FAF9",
        }
        for object_name, expected in expected_colors.items():
            with self.subTest(object_name=object_name):
                card = next(card for card in self.ui.cards
                            if card.objectName() == object_name)
                self.ui.select_reply(next(c.reply.source_index for c in self.ui.cards if c is not card))
                self.ui.replyScroll.ensureWidgetVisible(card)
                self.ui.app.processEvents()
                capture = self.ui.win.grab()
                dpr = capture.devicePixelRatio()
                image = capture.toImage().convertToFormat(QImage.Format.Format_RGB32)
                origin = card.mapTo(self.ui.win, QPoint(0, 0))
                left = max(0, math.floor(origin.x() * dpr))
                top = max(0, math.floor(origin.y() * dpr))
                right = min(image.width(), math.ceil((origin.x() + card.width()) * dpr))
                bottom = min(image.height(), math.ceil((origin.y() + card.height()) * dpr))
                self.assertLess(left, right, "card lies outside the captured image")
                self.assertLess(top, bottom, "card lies outside the captured image")
                expected_rgb = tuple(
                    int(expected[index:index + 2], 16) for index in (1, 3, 5)
                )
                matching_pixels = sum(
                    (pixel.red(), pixel.green(), pixel.blue()) == expected_rgb
                    for y in range(top, bottom)
                    for x in range(left, right)
                    for pixel in (image.pixelColor(x, y),)
                )
                self.assertGreater(
                    matching_pixels, 0,
                    f"{object_name} has no {expected} pixels in the window capture",
                )

    def test_reply_cards_do_not_emit_stylesheet_parse_warnings(self):
        from PySide6.QtCore import qInstallMessageHandler

        messages = []
        previous_handler = qInstallMessageHandler(
            lambda _kind, _context, message: messages.append(message)
        )
        try:
            self.ui.show(_result(
                candidates=("推荐", "候选一", "候选二", "候选三", "候选四"),
                reply_scores=(0.10, 0.50, 0.20, 0.15, 0.05),
                candidate_safety_probabilities=(0.8,) * 5,
                best_index=1,
            ))
            self.ui.app.processEvents()
            for card in self.ui.cards:
                card.ensurePolished()
                card.style().polish(card)
            self.ui.app.processEvents()
            self.ui.win.close()
            self.ui.app.processEvents()

            parse_warnings = [message for message in messages
                              if "Could not parse stylesheet" in message]
            self.assertEqual(parse_warnings, [])
        finally:
            qInstallMessageHandler(previous_handler)

    def test_grouped_replies_reflow_without_horizontal_overflow(self):
        result = _result(
            candidates=(
                "这是一条用于检查窄窗口换行的较长回复一",
                "这是一条用于检查窄窗口换行的较长回复二",
                "这是一条用于检查窄窗口换行的较长回复三",
                "这是一条用于检查窄窗口换行的较长回复四",
                "这是一条用于检查窄窗口换行的较长回复五",
            ),
            reply_scores=(0.10, 0.20, 0.50, 0.15, 0.05),
            candidate_safety_probabilities=(0.8,) * 5,
            best_index=2,
        )
        self.ui.show(result)
        self.ui.unusedToggle.click()

        for width in (720, 820, 1120):
            with self.subTest(width=width):
                self.ui.win.resize(width, 820)
                self.ui.app.processEvents()
                self.assertEqual(
                    self.ui.replyScroll.horizontalScrollBar().maximum(), 0
                )
                self.assertLessEqual(
                    self.ui.editorPanel.width(), self.ui.replyScroll.viewport().width()
                )
                for card in self.ui.cards:
                    if not card.isVisible():
                        continue
                    self.assertLessEqual(card.width(), self.ui.replyScroll.viewport().width())
                    self.assertTrue(card.rect().contains(card.copyButton.geometry()))
                    if card.fillButton.isVisible():
                        self.assertTrue(card.rect().contains(card.fillButton.geometry()))

    def test_rejected_replies_are_visible_for_copy_only(self):
        result = _result(
            candidates=("一", "二"),
            reply_scores=(0.5, 0.5),
            best_index=None,
            candidate_safety_probabilities=(0.3, 0.3),
        )
        self.ui.show(result)
        self.ui.app.processEvents()

        self.assertFalse(self.ui.unusedToggle.isVisible())
        self.assertFalse(self.ui.primaryReplyContent.isVisible())
        self.assertTrue(self.ui.otherReplyContent.isVisible())
        self.assertTrue(self.ui.replyStatus.isVisible())
        self.ui.unusedToggle.click()
        self.ui.app.processEvents()

        self.assertTrue(self.ui.otherReplyContent.isVisible())
        self.assertTrue(all(card.copyButton.isVisible() for card in self.ui.cards if card.isVisible()))
        self.assertTrue(all(not card.fillButton.isVisible() for card in self.ui.cards))
        self.assertTrue(all(card.scoreBadge.isVisible() for card in self.ui.cards if card.isVisible()))

    def test_pause_fallback_is_visible_and_other_candidates_expand_without_overflow(self):
        result = _result(
            candidates=("一", "二", "三", "四", "五"),
            reply_scores=(0.1, 0.1, 0.6, 0.1, 0.1),
            candidate_safety_probabilities=(0.8, 0.8, 0.8, 0.8, 0.8),
            best_index=2,
            reply_worthwhile_probability=0.2,
        )
        self.ui.show(result)
        self.ui.app.processEvents()

        fallback = next(card for card in self.ui.cards if card.reply.role == "fallback")
        self.assertTrue(self.ui.editorPanel.isVisible())
        self.assertTrue(self.ui.otherReplyContent.isVisible())
        self.assertEqual(sum(card.isVisible() for card in self.ui.cards), 4)
        self.assertTrue(self.ui.fillDraftButton.isVisible())
        self.assertEqual(self.ui.fillDraftButton.text(), "仍要填入微信（追加）")
        self.assertEqual(fallback.objectName(), "candidateReply")
        self.assertEqual(self.ui.replyStatus.text(), "本轮建议：先停在这里")

        self.ui.unusedToggle.click()
        self.ui.app.processEvents()
        self.assertTrue(self.ui.otherReplyContent.isVisible())
        self.assertFalse(self.ui.unusedToggle.isVisible())
        self.assertEqual(sum(card.isVisible() for card in self.ui.cards), 4)
        for width in (720, 820, 1120):
            with self.subTest(width=width):
                self.ui.win.resize(width, 820)
                self.ui.app.processEvents()
                self.assertEqual(self.ui.replyScroll.horizontalScrollBar().maximum(), 0)

    def test_unsafe_candidates_are_visible_copy_only(self):
        result = _result(
            best_index=None,
            candidate_safety_probabilities=(0.2, 0.4),
        )
        self.ui.show(result)
        self.ui.app.processEvents()
        self.assertFalse(self.ui.primaryReplyContent.isVisible())
        self.assertTrue(self.ui.otherReplyContent.isVisible())
        self.ui.unusedToggle.click()
        self.ui.app.processEvents()
        self.assertTrue(self.ui.otherReplyContent.isVisible())
        self.assertTrue(all(card.copyButton.isVisible() for card in self.ui.cards if card.isVisible()))
        self.assertTrue(all(not card.fillButton.isVisible() for card in self.ui.cards))
        self.assertTrue(all(card.reply.safe_to_send is False for card in self.ui.cards))

    def test_single_copy_only_candidate_uses_editor_without_empty_list(self):
        unsafe = _result(
            candidates=("候选一",), best_index=0, reply_scores=(1.0,),
            candidate_safety_probabilities=(0.2,),
        )
        for state in ("unsafe", "unreviewed"):
            with self.subTest(state=state):
                if state == "unsafe":
                    self.ui.show(unsafe)
                else:
                    self.ui.show_stage_error("Jev 判断", "合成失败", ["候选一"])
                self.ui.app.processEvents()
                self.assertFalse(self.ui.unusedToggle.isVisible())
                self.assertFalse(self.ui.otherReplyContent.isVisible())
                self.ui.unusedToggle.click()
                self.ui.app.processEvents()
                self.assertFalse(self.ui.otherReplyContent.isVisible())
                self.assertTrue(self.ui.copyDraftButton.isVisible())
                self.assertFalse(self.ui.cards[0].fillButton.isVisible())

    def test_new_result_removes_cards_from_both_reply_regions(self):
        self.ui.show(_result(reply_worthwhile_probability=0.2))
        old_cards = list(self.ui.cards)
        self.ui.show(_result(
            best_index=None,
            candidate_safety_probabilities=(0.2, 0.4),
        ))
        self.ui.app.processEvents()

        self.assertEqual(self.ui.primaryReplyBox.count(), 0)
        self.assertEqual(self.ui.otherReplyBox.count(), 2)
        self.assertEqual(len(self.ui.cards), 2)
        self.assertTrue(all(not card.isVisible() for card in old_cards))

    def test_tab_focused_button_gets_a_visible_focus_frame(self):
        self.ui.win.setFocus()
        self.ui.app.processEvents()

        focused = None
        for _ in range(12):
            self.QTest.keyClick(self.ui.win, self.Qt.Key_Tab)
            self.ui.app.processEvents()
            candidate = self.ui.app.focusWidget()
            if isinstance(candidate, self.QAbstractButton):
                focused = candidate
                break

        self.assertIsNotNone(focused)
        frame = getattr(self.ui, "focusFrame", None)
        self.assertIsNotNone(frame, "Tab focus needs a visible frame")
        self.assertIs(frame.widget(), focused)
        self.assertTrue(frame.isVisible())
        from app.ui_theme import PALETTE

        self.assertIn(PALETTE.primary.lower(), frame.styleSheet().lower())

    def test_focus_pages_keep_the_main_height_when_window_grows(self):
        self.ui._append_line()
        self.ui.win.resize(820, 820)
        self.ui.app.processEvents()
        first_height = self.ui.focusPages.height()
        self.assertGreater(first_height, 300)

        self.ui.win.resize(820, 1000)
        self.ui.app.processEvents()
        self.assertGreater(self.ui.focusPages.height(), first_height)
        self.assertEqual(self.ui.replyScroll.maximumHeight(), 16777215)

    def test_view_tabs_are_equal_width_and_keyboard_operable(self):
        self.ui._append_line()
        self.ui.win.resize(820, 820)
        self.ui.app.processEvents()
        self.assertLessEqual(abs(self.ui.analysisTab.width() - self.ui.replyTab.width()), 2)

        self.ui.replyTab.setFocus()
        self.QTest.keyClick(self.ui.replyTab, self.Qt.Key_Space)
        self.ui.app.processEvents()
        self.assertEqual(self.ui.focusPages.currentIndex(), 1)
        self.assertTrue(self.ui.replyTab.isChecked())

        self.ui.analysisTab.setFocus()
        self.QTest.keyClick(self.ui.analysisTab, self.Qt.Key_Space)
        self.ui.app.processEvents()
        self.assertEqual(self.ui.focusPages.currentIndex(), 0)
        self.assertTrue(self.ui.analysisTab.isChecked())
        self.assertIs(self.ui.app.focusWidget(), self.ui.previewScroll)

    def test_analysis_tab_focuses_timeline_scroll_for_keyboard_navigation(self):
        self.ui._append_line()
        self.ui.win.resize(820, 820)
        self.ui.centerPages.setCurrentWidget(self.ui.timeline)
        self.ui.timeline.content.setMinimumHeight(2000)
        self.ui.app.processEvents()
        bar = self.ui.timeline.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)

        self.ui.replyTab.setFocus()
        self.QTest.keyClick(self.ui.replyTab, self.Qt.Key_Space)
        self.ui.app.processEvents()
        self.ui.analysisTab.setFocus()
        self.QTest.keyClick(self.ui.analysisTab, self.Qt.Key_Space)
        self.ui.app.processEvents()

        self.assertEqual(self.ui.focusPages.currentIndex(), 0)
        self.assertIs(self.ui.app.focusWidget(), self.ui.timeline.scroll)
        bar.setValue(0)
        self.QTest.keyClick(self.ui.timeline.scroll, self.Qt.Key_PageDown)
        self.ui.app.processEvents()
        self.assertGreater(bar.value(), 0)


if __name__ == "__main__":
    unittest.main()
