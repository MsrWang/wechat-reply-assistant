"""Native geometry and local navigation regression checks; fictional data only."""
import sys
import unittest
from unittest.mock import patch

from tests.test_result_view import _result


@unittest.skipUnless(sys.platform == "win32", "requires Windows Qt")
class DesktopWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        for font in ("simhei.ttf", "msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)

    def setUp(self):
        from app import settings
        from app.overlay import Overlay
        self.patches = [patch.object(settings, "load_preferences", return_value=settings.Preferences()),
                        patch.object(settings, "migrate_legacy_preferences", return_value=False)]
        for item in self.patches:
            item.start()
        self.calls = []
        self.ui = Overlay(lambda: self.calls.append("read"), lambda *a: self.calls.append("analyze"),
                          lambda *a: self.calls.append("fill"), lambda: None,
                          lambda *a: False, lambda: None, lambda: False)
        self.ui.win.resize(1120, 800)
        self.ui.show(_result())
        self.ui.app.processEvents()

    def tearDown(self):
        self.ui.win.close()
        self.ui.app.processEvents()
        for item in self.patches:
            item.stop()

    def test_wide_context_and_draft_remain_visible_together(self):
        self.assertTrue(self.ui.analysisView.isVisible())
        self.assertTrue(self.ui.replyView.isVisible())
        self.assertGreater(self.ui.draftEdit.height(), 160)

    def test_alternatives_are_visible_without_expansion_and_selected_is_not_duplicated(self):
        for count in (1, 2, 5):
            self.ui.show(_result(candidates=tuple("合成回复" + str(i) for i in range(count)),
                                 reply_scores=tuple(1 / count for _ in range(count)),
                                 candidate_safety_probabilities=tuple(.9 for _ in range(count)), best_index=0))
            for width, height in ((1120, 800), (720, 620)):
                self.ui.win.resize(width, height)
                self.ui.app.processEvents()
                self.assertFalse(self.ui.unusedToggle.isVisible())
                self.assertEqual(self.ui.otherReplyContent.isVisible(), count > 1)
                visible = [card.reply.source_index for card in self.ui.cards if card.isVisible()]
                self.assertEqual(visible, list(range(1, count)))
                self.assertGreater(self.ui.draftEdit.height(), 90)
                self.assertTrue(self.ui.copyDraftButton.isVisible())
                self.assertTrue(self.ui.win.rect().contains(self.ui.copyDraftButton.mapTo(self.ui.win, self.ui.copyDraftButton.rect().bottomRight())))

    def test_text_fonts_cover_dynamic_widgets_and_popup(self):
        from PySide6.QtWidgets import QLabel
        from PySide6.QtGui import QFontInfo
        from app.preview_model import PreviewModel
        from app.privacy import ChatLine
        self.ui.set_preview(PreviewModel("合成会话", [ChatLine("her", "合成消息")]))
        self.ui.show(_result())
        widgets = [self.ui.chatTitleLabel, self.ui.readButton, self.ui.titleEdit,
                   self.ui.draftEdit, self.ui.workspaceButton, self.ui.cards[1].text,
                   *self.ui.timeline.findChildren(QLabel)]
        for widget in widgets:
            self.assertEqual(QFontInfo(widget.font()).family(), "SimHei", type(widget).__name__)
        combo = self.ui.relationshipBox
        combo._showComboMenu()
        self.ui.app.processEvents()
        self.assertEqual(QFontInfo(combo.dropMenu.view.font()).family(), "SimHei")
        combo.dropMenu.close()

    def test_normal_header_is_quiet_but_warnings_and_stop_remain(self):
        self.assertFalse(self.ui.readTimeLabel.isVisible())
        self.assertFalse(self.ui.sourceHint.isVisible())
        self.assertFalse(self.ui.status.isVisible())
        self.ui.set_status("暂时无法确认微信内容", "warning")
        self.assertTrue(self.ui.status.isVisible())
        self.ui.show(_result(reply_worthwhile_probability=.2))
        self.assertTrue(self.ui.replyStatus.isVisible())

    def test_copy_only_status_is_visible_after_rendering(self):
        self.ui.show(_result(), allow_fill=False)
        self.assertTrue(self.ui.status.isVisible())
        self.assertFalse(self.ui.fillDraftButton.isVisible())

    def test_version_and_context_details_do_not_take_normal_workspace_rows(self):
        from app.preview_model import PreviewModel
        from app.privacy import ChatLine
        self.ui.set_preview(PreviewModel("合成会话", [ChatLine("her", "合成消息")]))
        self.assertFalse(self.ui.versionLabel.isVisible())
        self.assertFalse(self.ui.previewContext.isVisible())
        self.assertTrue(self.ui.chatTitleLabel.toolTip())
        self.ui.open_settings()
        self.assertTrue(self.ui.versionLabel.isVisible())

    def test_switching_preserves_edits_and_source_indices(self):
        ui = self.ui
        ui.show(_result(best_index=1), allow_fill=False)
        ui.draftEdit.setPlainText("第二条修改")
        ui.select_reply(0)
        ui.draftEdit.setPlainText("第一条修改")
        ui.select_reply(1)
        self.assertEqual(ui.draftEdit.toPlainText(), "第二条修改")
        self.assertFalse(ui.fillDraftButton.isEnabled())
        self.assertEqual([c.reply.source_index for c in ui.cards if not c.isHidden()], [0])
        edited_alternative = next(c for c in ui.cards if c.reply.source_index == 0)
        self.assertEqual(edited_alternative.text.text(), "第一条修改")
        self.assertFalse(edited_alternative.scoreBadge.isVisible())
        ui.app.processEvents()
        self.assertGreaterEqual(edited_alternative.roleLabel.width(),
                                edited_alternative.roleLabel.fontMetrics().horizontalAdvance(edited_alternative.roleLabel.text()))
        edited_alternative.copyButton.click()
        self.assertEqual(ui.app.clipboard().text(), edited_alternative.text.text())

    def test_font_fallback_uses_system_default_when_chinese_fonts_absent(self):
        from PySide6.QtGui import QFontDatabase
        from app.ui_theme import configure_text_fonts
        try:
            with patch.object(QFontDatabase, "families", return_value=[]):
                configure_text_fonts(self.app)
            self.assertEqual(self.app.font().families()[0],
                             QFontDatabase.systemFont(QFontDatabase.GeneralFont).family())
            with patch.object(QFontDatabase, "families", return_value=["Microsoft YaHei"]):
                configure_text_fonts(self.app)
            self.assertEqual(self.app.font().families()[0], "Microsoft YaHei")
        finally:
            configure_text_fonts(self.app)

    def test_waiting_and_copy_feedback_survive_normal_hint_reduction(self):
        self.ui.copyDraftButton.click()
        self.assertTrue(self.ui.status.isVisible())
        self.ui.show_waiting_for_reply("已读取到你刚发出的消息，等待对方回复。")
        self.ui.app.processEvents()
        self.assertTrue(self.ui.status.isVisible())

    def test_repeated_compact_roundtrips_preserve_user_dragged_pane_balance(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        splitter = self.ui.workspaceSplitter
        handle = splitter.handle(1)
        center = handle.rect().center()
        QTest.mousePress(handle, Qt.LeftButton, pos=center)
        QTest.mouseMove(handle, center + QPoint(50, 0), delay=20)
        QTest.mouseRelease(handle, Qt.LeftButton, pos=center + QPoint(50, 0))
        self.ui.app.processEvents()
        before = splitter.sizes()
        expected = before[0] / sum(before)
        for _ in range(5):
            self.ui.win.resize(720, 620)
            self.ui.app.processEvents()
            self.ui.win.resize(1120, 800)
            self.ui.app.processEvents()
            sizes = splitter.sizes()
            self.assertAlmostEqual(sizes[0] / sum(sizes), expected, delta=.025)

    def test_sidebar_distinguishes_unverified_saved_and_temporary_key(self):
        ui = self.ui
        for saved, temporary, expected in (
            (None, "", "Key 状态待确认"),
            (True, "", "Key 已保存"),
            (False, "", "Key 未设置"),
            (None, "temporary-test-value", "临时 Key 已设置"),
        ):
            with self.subTest(saved=saved, temporary=bool(temporary)):
                ui._saved_key_present = saved
                ui.keyEdit.setText(temporary)
                ui._sync_actions()
                self.assertEqual(ui.connectionLabel.text(), expected)
        self.assertTrue(ui._has_key())

    def test_open_settings_refreshes_sidebar_after_key_status_callback(self):
        ui = self.ui
        for known, expected_state, expected_sidebar in (
            (True, "已保存", "Key 已保存"),
            (False, "未设置", "Key 未设置"),
        ):
            with self.subTest(known=known):
                ui._saved_key_present = not known
                ui._sync_actions()
                ui.on_key_status = lambda value=known: value
                ui.open_settings()
                self.assertEqual(ui.keyState.text(), expected_state)
                self.assertEqual(ui.connectionLabel.text(), expected_sidebar)
                ui._back_home()

    def test_demo_navigation_tracks_real_clear_callback_and_exit(self):
        ui = self.ui
        def clear_with_receipt():
            self.calls.append("clear")
            ui.clear_chat_fields()
        ui.on_clear = clear_with_receipt
        ui.navDemoButton.click()
        self.assertEqual(self.calls, ["clear"])
        self.assertTrue(ui._demo)
        self.assertIs(ui.pages.currentWidget(), ui.home)
        self.assertTrue(ui.navDemoButton.isChecked())
        self.assertFalse(ui.workspaceButton.isChecked())
        self.assertFalse(ui.fillDraftButton.isEnabled())
        self.assertFalse(ui.analyzeButton.isEnabled())
        ui.clearButton.click()
        self.assertEqual(self.calls, ["clear", "clear"])
        self.assertFalse(ui._demo)
        self.assertTrue(ui.workspaceButton.isChecked())
        self.assertFalse(ui.navDemoButton.isChecked())

    def test_compact_switch_and_settings_preserve_edit_without_callbacks(self):
        ui = self.ui
        ui.draftEdit.setPlainText("这是我的修改")
        ui.win.resize(820, 680)
        ui.app.processEvents()
        self.assertTrue(ui._compact)
        ui.analysisTab.click()
        self.assertTrue(ui.analysisView.isVisible())
        self.assertFalse(ui.replyView.isVisible())
        ui.replyTab.click()
        ui.open_settings()
        ui._back_home()
        self.assertEqual(ui.draftEdit.toPlainText(), "这是我的修改")
        self.assertFalse(self.calls)

    def test_long_content_keeps_primary_actions_outside_scroll_and_in_bounds(self):
        ui = self.ui
        ui.draftEdit.setPlainText("这是一段很长的回复。" * 300)
        ui.unusedToggle.setChecked(True)
        ui.app.processEvents()
        self.assertFalse(ui.previewScroll.isAncestorOf(ui.analyzeButton))
        self.assertFalse(ui.replyScroll.isAncestorOf(ui.fillDraftButton))
        for button in (ui.analyzeButton, ui.copyDraftButton, ui.fillDraftButton):
            self.assertTrue(button.isVisible())
            self.assertTrue(ui.win.rect().contains(button.mapTo(ui.win, button.rect().bottomRight())))

    def test_native_window_can_resize_and_maximize(self):
        from PySide6.QtCore import Qt
        flags = self.ui.win.windowFlags()
        self.assertFalse(flags & Qt.FramelessWindowHint)
        self.assertFalse(flags & Qt.WindowStaysOnTopHint)
        self.assertTrue(flags & Qt.WindowMaximizeButtonHint)
        self.ui.win.resize(1280, 850)
        self.ui.app.processEvents()
        self.assertEqual(self.ui.win.width(), 1280)

    def test_minimum_window_with_refinement_keeps_actions_reachable(self):
        ui = self.ui
        ui.win.resize(720, 620)
        ui.refineToggle.click()
        ui.app.processEvents()
        self.assertEqual(ui.win.size().width(), 720)
        self.assertEqual(ui.win.size().height(), 620)
        self.assertTrue(ui.fillDraftButton.isVisible())
        self.assertTrue(ui.win.rect().contains(ui.fillDraftButton.mapTo(ui.win, ui.fillDraftButton.rect().bottomRight())))

    def test_expanding_alternatives_leaves_one_whole_candidate_visible(self):
        ui = self.ui
        ui.unusedToggle.click()
        ui.app.processEvents()
        card = next(card for card in ui.cards if card.isVisible())
        self.assertGreaterEqual(ui.replyScroll.viewport().height(), card.height() + 60)
        self.assertGreaterEqual(ui.draftEdit.height(), 160)

    def test_long_title_does_not_take_over_the_workspace(self):
        from app.preview_model import PreviewModel
        from app.privacy import ChatLine
        self.ui.win.resize(820, 680)
        self.ui.set_preview(PreviewModel("很长的会话标题" * 100, [ChatLine("her", "合成消息")]))
        self.ui.app.processEvents()
        self.assertLess(self.ui.headerPanel.height(), 140)
        self.assertGreater(self.ui.previewScroll.viewport().height(), 200)

    def test_compact_alternatives_have_room_for_complete_first_alternative(self):
        ui = self.ui
        for width, height in ((820, 680), (720, 620)):
            ui.win.resize(width, height)
            ui.unusedToggle.setChecked(True)
            ui.app.processEvents()
            first = next(card for card in ui.cards if card.isVisible())
            self.assertNotEqual(first.reply.source_index, ui._selected_index)
            viewport = ui.replyScroll.viewport()
            corner = first.mapTo(viewport, first.rect().bottomRight())
            self.assertTrue(viewport.rect().contains(corner), f"{width}x{height}: viewport={viewport.rect()}, card corner={corner}")
            self.assertGreaterEqual(ui.draftEdit.height(), 90)
