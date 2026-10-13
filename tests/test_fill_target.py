import unittest
from unittest.mock import patch


class FillTargetTests(unittest.TestCase):
    def make_target(self, *, title="小王", size=(900, 700), dpi=120, process="weixin.exe"):
        from app.fill_target import FillTarget

        return FillTarget(
            hwnd=7,
            pid=88,
            process_name=process,
            area=(20, 80, 880, 540),
            window_size=size,
            dpi=dpi,
            chat_title=title,
        )

    def test_target_rejects_chat_size_dpi_process_and_area_drift(self):
        from app.fill_target import FillTargetError, validate_target

        expected = self.make_target()
        changed_area = self.make_target()
        object.__setattr__(changed_area, "area", (21, 80, 880, 540))
        for current in (
            self.make_target(title="小李"),
            self.make_target(size=(1000, 700)),
            self.make_target(dpi=144),
            self.make_target(process="other.exe"),
            changed_area,
        ):
            with self.subTest(current=current):
                with self.assertRaises(FillTargetError):
                    validate_target(expected, current)

    def test_similar_ocr_title_can_be_accepted_by_explicit_matcher(self):
        from app.fill_target import validate_target

        validate_target(
            self.make_target(title="不好意思"),
            self.make_target(title="不好竟思"),
            title_matcher=lambda left, right: True,
        )

    def test_capture_accepts_only_known_wechat_processes(self):
        from app import fill_target

        backend = _FakeTargetBackend(process_name="malware.exe")
        with patch.object(fill_target, "_backend", return_value=backend):
            with self.assertRaises(fill_target.FillTargetError):
                fill_target.capture_target(7, (20, 80, 880, 540), "小王")


class _FakeTargetBackend:
    def __init__(self, process_name="weixin.exe"):
        self.process_name = process_name

    def snapshot(self, hwnd):
        return {
            "hwnd": hwnd,
            "pid": 88,
            "process_name": self.process_name,
            "window_size": (900, 700),
            "dpi": 120,
        }


if __name__ == "__main__":
    unittest.main()
