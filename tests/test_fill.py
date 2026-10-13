import ctypes
import importlib
import sys
import unittest
from unittest.mock import Mock, patch

from app.fill_target import FillTarget, FillTargetError


class _FakeWindowsLibraries:
    def __init__(self):
        self.user32 = Mock()
        self.kernel32 = Mock()
        self.dwmapi = Mock()


def _target():
    return FillTarget(
        hwnd=7,
        pid=88,
        process_name="weixin.exe",
        area=(20, 80, 880, 540),
        window_size=(900, 700),
        dpi=120,
        chat_title="小王",
    )


class FillTests(unittest.TestCase):
    def setUp(self):
        self.windows = _FakeWindowsLibraries()
        self.windll = patch.object(ctypes, "windll", self.windows, create=True)
        self.windll.start()
        self.addCleanup(self.windll.stop)
        sys.modules.pop("app.fill", None)
        self.fill = importlib.import_module("app.fill")

    def tearDown(self):
        sys.modules.pop("app.fill", None)

    def test_invalid_target_performs_no_clipboard_mouse_or_keyboard_action(self):
        with patch.object(
            self.fill, "validate_live_window", side_effect=FillTargetError()
        ), patch.object(self.fill, "bring_to_foreground") as foreground, patch.object(
            self.fill, "click_input_region"
        ) as click, patch.object(self.fill, "set_clipboard") as clipboard, patch.object(
            self.fill, "_paste"
        ) as paste:
            with self.assertRaises(FillTargetError):
                self.fill.fill_validated(_target(), "候选")

        foreground.assert_not_called()
        click.assert_not_called()
        clipboard.assert_not_called()
        paste.assert_not_called()

    def test_extended_frame_api_declares_pointer_safe_64_bit_signature(self):
        def populate(_hwnd, _attribute, pointer, _size):
            pointer._obj.left = 10
            pointer._obj.top = 20
            pointer._obj.right = 910
            pointer._obj.bottom = 720
            return 0

        self.windows.dwmapi.DwmGetWindowAttribute.side_effect = populate

        rect = self.fill._window_rect(0x123456789)

        self.assertEqual((rect.left, rect.top, rect.right, rect.bottom), (10, 20, 910, 720))
        self.assertEqual(
            self.windows.dwmapi.DwmGetWindowAttribute.argtypes[0], ctypes.wintypes.HWND
        )

    def test_fill_executor_uses_guarded_order_and_never_sends_enter(self):
        actions = []
        with patch.object(self.fill, "validate_live_window", side_effect=lambda _: actions.append("validate")), \
             patch.object(self.fill, "bring_to_foreground", side_effect=lambda _: actions.append("foreground")), \
             patch.object(self.fill, "assert_foreground", side_effect=lambda _: actions.append("assert")), \
             patch.object(self.fill, "click_input_region", side_effect=lambda _: actions.append("click")), \
             patch.object(self.fill, "_move_to_end", side_effect=lambda: actions.append("end")), \
             patch.object(self.fill, "set_clipboard", side_effect=lambda _: actions.append("clipboard")), \
             patch.object(self.fill, "_paste", side_effect=lambda: actions.append("paste")):
            self.fill.fill_validated(_target(), "候选")

        self.assertEqual(actions, [
            "validate", "foreground", "assert", "click", "assert", "end", "assert",
            "clipboard", "assert", "paste",
        ])

    def test_paste_chord_contains_only_control_and_v(self):
        with patch.object(self.fill.time, "sleep"):
            self.fill._paste()
        keys = [call.args[0] for call in self.windows.user32.keybd_event.call_args_list]
        self.assertEqual(keys, [0x11, 0x56, 0x56, 0x11])
        self.assertNotIn(0x0D, keys)


if __name__ == "__main__":
    unittest.main()
