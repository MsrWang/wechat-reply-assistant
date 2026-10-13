"""Native Qt/controller workflows with synthetic external boundaries only.

These checks do not constitute live WeChat capture/OCR or user acceptance.
"""
import os
import sys
import unittest
from unittest.mock import patch

import tests.test_source_contract as contract
from tests.test_engine import jev_response


@unittest.skipUnless(sys.platform == "win32", "requires native Windows Qt")
class NativeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QTimer, QCoreApplication, QEvent
        from PySide6.QtTest import QTest
        from app.overlay import Overlay
        from app import settings
        cls.Overlay, cls.QTimer, cls.QTest = Overlay, QTimer, QTest
        cls.QCoreApplication, cls.QEvent, cls.settings = QCoreApplication, QEvent, settings

    def setUp(self):
        # Composition deliberately avoids rediscovering the legacy TestCase suite.
        self.boundary = contract.ManualControllerTests()
        self.boundary.setUp()
        self.addCleanup(self.boundary.doCleanups)
        self.main = self.boundary.controller
        self.timers = []
        for name, value in (("load_preferences", self.settings.Preferences(relationship="朋友")),
                            ("migrate_legacy_preferences", False)):
            p = patch.object(self.settings, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(self.Overlay, "after", self.owned_after)
        p.start()
        self.addCleanup(p.stop)
        self.ui = self.make_overlay()
        self.main.ov = self.ui
        self.ui.keyEdit.setText("synthetic-key")
        self.ui.after(1, self.main.drain_events)
        self.candidates = ("在呀", "怎么啦")
        self.boundary.draft.return_value = list(self.candidates)
        self.boundary.ask.return_value = jev_response(candidates=self.candidates)

    def owned_after(self, ms, callback):
        # Owned real Qt timers prevent callbacks crossing module reload/teardown.
        timer = self.QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        self.timers.append(timer)
        timer.start(ms)

    def make_overlay(self):
        m = self.main
        return self.Overlay(m.on_read, m.on_analyze, m.on_fill, m.on_clear,
                            m.on_save_key, m.on_forget_key, m.on_key_status, m.on_rewrite)

    def tearDown(self):
        for timer in self.timers:
            timer.stop()
            timer.timeout.disconnect()
            timer.deleteLater()
        # Flush the UI's own layout/scroll singleShots while widgets still exist.
        self.QTest.qWait(65)
        self.ui.app.focusChanged.disconnect(self.ui._update_focus_frame)
        self.ui.win.close()
        self.ui.win.deleteLater()
        self.QCoreApplication.sendPostedEvents(None, self.QEvent.DeferredDelete)
        self.ui.app.processEvents()

    def pump(self):
        self.QTest.qWait(65)

    def finish(self):
        self.boundary.jobs[-1].run()
        self.pump()

    def generate(self):
        self.ui.readButton.click()
        self.finish()
        self.assertEqual(self.ui.preview.lines[0].text, "在吗")
        self.assertTrue(self.ui.analyzeButton.isEnabled())
        self.ui.analyzeButton.click()
        self.assertFalse(self.ui.fillDraftButton.isEnabled())
        self.finish()
        self.assertIsNotNone(self.main.session.result)
        self.assertTrue(self.ui.fillDraftButton.isEnabled())

    def select(self, index):
        if self.ui._selected_index == index:
            return
        button = next(card for card in self.ui.cards if card.reply.source_index == index).selectButton
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        button.click()
        self.assertEqual(self.ui._selected_index, index)

    def rewrite(self):
        if not self.ui.refineToggle.isChecked():
            self.ui.refineToggle.click()
        self.assertTrue(self.ui.refineButtons[0].isVisible())
        self.assertTrue(self.ui.refineButtons[0].isEnabled())
        self.ui.refineButtons[0].click()

    def test_partial_verification_retains_selection_edits_and_recovers_once(self):
        self.generate()
        self.select(0)
        self.ui.draftEdit.setPlainText("第一条编辑")
        self.select(1)
        self.ui.draftEdit.setPlainText("第二条编辑")
        before = (self.main.session.result, dict(self.ui._drafts), self.ui._selected_index)
        self.boundary.reader.read.return_value = [("her", "张三", "在", 90)]
        self.ui.fillDraftButton.click()
        self.assertEqual((self.main.session.result, self.ui._drafts, self.ui._selected_index), before)
        self.assertIn("暂时无法确认", self.ui.status.text())
        self.assertIn("重试", self.ui.status.text())
        self.assertIn("重新读取", self.ui.status.text())
        self.assertFalse(self.main.session.fill_consumed)
        self.assertTrue(self.ui.fillDraftButton.isEnabled())
        self.boundary.fill.assert_not_called()
        for width in (1120, 720, 1120):
            self.ui.win.resize(width, 800)
            self.pump()
            self.assertEqual(self.ui._drafts, before[1])
            self.assertEqual(self.ui._selected_index, 1)
            self.assertEqual(self.ui.draftEdit.toPlainText(), "第二条编辑")
            self.assertTrue(self.ui.fillDraftButton.isEnabled())
            self.assertTrue(all(row.fill_allowed for row in self.ui._reply_views.values()))
        self.boundary.reader.read.return_value = [("her", "张三", "在吗", 90)]
        self.ui.fillDraftButton.click()
        self.boundary.fill.assert_called_once_with(self.main.session.fill_target, "第二条编辑")
        self.assertTrue(self.main.session.fill_consumed)
        self.assertFalse(self.ui.fillDraftButton.isEnabled())
        self.assertTrue(self.ui.copyDraftButton.isEnabled())
        self.select(0)
        self.ui.draftEdit.setPlainText("恢复填入后新编辑")
        self.assertEqual(self.ui.draftEdit.toPlainText(), "恢复填入后新编辑")
        self.assertTrue(self.main.session.fill_consumed)
        self.assertFalse(self.ui.fillDraftButton.isEnabled())
        self.ui.fillDraftButton.click()
        self.main.on_fill(self.ui.draftEdit.toPlainText())
        self.assertTrue(self.main.session.fill_consumed)
        self.assertFalse(self.ui.fillDraftButton.isEnabled())
        self.boundary.fill.assert_called_once_with(self.main.session.fill_target, "第二条编辑")

    def test_similar_title_is_stale_copy_only(self):
        self.generate()
        self.boundary.ocr.read_title.return_value = "当前聊天2"
        self.ui.fillDraftButton.click()
        self.assert_stale()

    def test_exact_prefix_new_message_is_stale_copy_only(self):
        self.generate()
        self.boundary.reader.read.return_value = [("her", "张三", "在吗", 90),
                                                  ("her", "张三", "新消息", 90)]
        self.ui.fillDraftButton.click()
        self.assert_stale()

    def assert_stale(self):
        self.boundary.fill.assert_not_called()
        self.assertIsNone(self.main.session.result)
        self.assertIn("重新读取", self.ui.status.text())
        self.assertFalse(self.ui.fillDraftButton.isEnabled())
        self.assertTrue(self.ui.copyDraftButton.isEnabled())
        self.assertTrue(all(not row.fill_allowed for row in self.ui._reply_views.values()))

    def test_generation_and_rewrite_use_snapshot_with_capture_unavailable(self):
        self.ui.readButton.click()
        self.finish()
        calls = self.boundary.capture_once.call_count
        self.boundary.capture_once.side_effect = RuntimeError("synthetic occlusion")
        self.ui.analyzeButton.click()
        self.finish()
        self.assertTrue(self.ui.fillDraftButton.isEnabled())
        frozen = self.main.session.confirmed
        self.rewrite()
        self.finish()
        self.assertIs(self.main.session.confirmed, frozen)
        self.assertEqual(self.boundary.capture_once.call_count, calls)
        self.assertEqual(self.boundary.analyze.call_count, 2)
        self.assertIsNotNone(self.main.session.result)

    def test_clear_discards_queued_result_and_late_worker(self):
        self.generate()
        self.rewrite()
        late = self.boundary.jobs[-1]
        self.ui.clearButton.click()
        late.run()
        self.pump()
        self.assertIsNone(self.main.session.result)
        self.assertEqual(self.ui.cands, [])
        self.assertEqual(self.ui.draftEdit.toPlainText(), "")
        self.boundary.fill.assert_not_called()
        self.ui.keyEdit.setText("synthetic-key")
        self.generate()
        self.rewrite()
        self.boundary.jobs[-1].run()  # queue an analysis, clear before Qt delivers it
        self.assertTrue(self.ui.clearButton.isVisible())
        self.ui.clearButton.click()
        self.pump()
        self.assertFalse(self.ui.preview.lines)
        self.assertEqual(self.ui.cands, [])
        self.assertIsNone(self.main.session.result)

    def test_reread_discards_late_worker_after_native_clear(self):
        self.generate()
        self.rewrite()
        late = self.boundary.jobs[-1]
        self.ui.clearButton.click()  # read is deliberately disabled during a worker
        self.ui.readButton.click()
        late.run()
        self.finish()
        self.assertEqual(self.ui.preview.lines[0].text, "在吗")
        self.assertEqual(self.ui.cands, [])
        self.assertIsNone(self.main.session.result)
        self.ui.keyEdit.setText("synthetic-key")  # clear intentionally drops temporary Key
        self.assertTrue(self.ui.analyzeButton.isEnabled())
        self.boundary.fill.assert_not_called()

    def test_actual_application_close_finally_discards_late_worker_and_queue(self):
        self.close_application_with_pending_read(complete_before_close=False)

    def test_actual_application_close_discards_already_queued_read(self):
        self.close_application_with_pending_read(complete_before_close=True)

    def close_application_with_pending_read(self, *, complete_before_close):
        # Run actual Overlay.run -> QApplication.exec -> window close -> main finally.
        self.ui.app.focusChanged.disconnect(self.ui._update_focus_frame)
        self.ui.win.close()
        old_ui = self.ui
        def act():
            self.ui = self.main.ov
            self.ui.keyEdit.setText("synthetic-key")
            self.ui.readButton.click()
            if complete_before_close:
                self.boundary.jobs[-1].run()  # queue read before event-loop dispatch
            self.ui.win.close()
        self.owned_after(10, act)
        self.main._run_application()
        old_ui.win.deleteLater()
        self.assertTrue(self.main._closed)
        self.assertTrue(self.main.events.empty())
        self.assertEqual(self.main.session.preview_lines, [])
        self.assertFalse(self.ui.preview.lines)
        if not complete_before_close:
            self.boundary.jobs[-1].run()
        self.pump()
        self.assertIsNone(self.main.session.result)
        self.assertFalse(self.ui.preview.lines)
        self.main._post_event(self.main._generation, "analysis", object())
        self.assertTrue(self.main.events.empty())
        self.boundary.fill.assert_not_called()
