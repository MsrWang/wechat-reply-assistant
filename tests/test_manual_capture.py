import unittest
from unittest.mock import Mock

from app.manual_capture import CaptureTimeout, capture_once


class FakeCapture:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.stopped = False

    def settled(self):
        return next(self.frames, None)

    def stop(self):
        self.stopped = True


class ManualCaptureTests(unittest.TestCase):
    def test_returns_first_stable_frame_and_always_stops(self):
        capture = FakeCapture([None, "frame"])
        result = capture_once(
            9,
            capture_factory=lambda _: capture,
            area_locator=lambda frame: (1, 2, 3, 4, 5, 6),
            clock=Mock(side_effect=[0.0, 0.1, 0.2]),
            sleep=lambda _: None,
            timeout=1.0,
        )
        self.assertEqual(result, ("frame", (1, 2, 3, 4, 5, 6)))
        self.assertTrue(capture.stopped)

    def test_timeout_stops_capture(self):
        capture = FakeCapture([])
        with self.assertRaises(CaptureTimeout):
            capture_once(
                9,
                capture_factory=lambda _: capture,
                area_locator=lambda _: None,
                clock=Mock(side_effect=[0.0, 2.0]),
                sleep=lambda _: None,
                timeout=1.0,
            )
        self.assertTrue(capture.stopped)

    def test_clock_error_stops_capture(self):
        capture = FakeCapture([])
        with self.assertRaisesRegex(RuntimeError, "clock failed"):
            capture_once(
                9,
                capture_factory=lambda _: capture,
                area_locator=lambda _: None,
                clock=Mock(side_effect=RuntimeError("clock failed")),
                sleep=lambda _: None,
            )
        self.assertTrue(capture.stopped)

    def test_locator_error_stops_capture(self):
        capture = FakeCapture(["frame"])
        with self.assertRaisesRegex(ValueError, "locator failed"):
            capture_once(
                9,
                capture_factory=lambda _: capture,
                area_locator=Mock(side_effect=ValueError("locator failed")),
                clock=Mock(return_value=0.0),
                sleep=lambda _: None,
            )
        self.assertTrue(capture.stopped)


if __name__ == "__main__":
    unittest.main()
