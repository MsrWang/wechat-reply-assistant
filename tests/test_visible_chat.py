import gc
import importlib
import unittest
import weakref
from types import SimpleNamespace
from unittest.mock import Mock

from app.fill_target import FillTarget
from app.privacy import ChatLine


class _Frame:
    def __getitem__(self, region):
        return region


class VisibleChatTests(unittest.TestCase):
    def setUp(self):
        # A missing capture path must fail as an explicit feature assertion.
        try:
            module = importlib.import_module("app.visible_chat")
        except ModuleNotFoundError:
            self.fail("reusable visible-chat capture is not implemented")
        self.capture = module.capture_visible_chat
        self.references = []

        def capture_once(hwnd):
            self.assertEqual(hwnd, 7)
            frame = _Frame()
            self.references.append(weakref.ref(frame))
            return frame, (10, 60, 400, 500, "bg", 5)

        self.capture_once = capture_once
        self.reader = SimpleNamespace(read=Mock(return_value=[("her", "张三", "在吗", 90)]))
        self.read_title = Mock(return_value="当前聊天")
        self.target = Mock(side_effect=lambda hwnd, area, title: FillTarget(
            hwnd, 88, "weixin.exe", area, (900, 700), 120, title
        ))

    def run_capture(self):
        return self.capture(7, capture_once_fn=self.capture_once,
                            reader_factory=lambda: self.reader,
                            read_title_fn=self.read_title, capture_target_fn=self.target)

    def test_capture_visible_chat_returns_text_only_and_releases_frame(self):
        raw, target = self.run_capture()
        self.assertEqual(raw.title, "当前聊天")
        self.assertEqual(raw.lines, (ChatLine("her", "在吗", "张三"),))
        self.assertEqual(raw.area, (10, 60, 400, 500))
        self.assertEqual(target.chat_title, "当前聊天")
        self.assertFalse(hasattr(raw, "frame"))
        self.assertFalse(hasattr(raw, "crop"))
        self.assertIsNotNone(raw.read_at.utcoffset())
        self.read_title.assert_called_once_with((slice(5, 60), slice(10, 400)))
        self.reader.read.assert_called_once_with((slice(60, 500), slice(10, 400)), "bg")
        gc.collect()
        self.assertIsNone(self.references[0]())

    def test_empty_title_retains_editable_text_without_fill_target(self):
        self.read_title.return_value = "  "
        raw, target = self.run_capture()
        self.assertIsNone(target)
        self.assertEqual(raw.lines[0].text, "在吗")
        self.target.assert_not_called()

    def test_failed_ocr_releases_frame_even_while_exception_is_retained(self):
        # A callback that retains no input lets us inspect this function's ownership.
        def fail_read(crop, background):
            raise ValueError("synthetic OCR failure")

        self.reader.read = fail_read
        retained_error = None
        try:
            self.run_capture()
        except ValueError as error:
            retained_error = error
        gc.collect()
        self.assertIsNotNone(retained_error)
        self.assertIsNotNone(retained_error.__traceback__)
        self.assertIsNone(self.references[0]())


if __name__ == "__main__":
    unittest.main()
