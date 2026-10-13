"""One-shot, in-memory capture of a stable WeChat chat area."""
import time


class CaptureTimeout(RuntimeError):
    pass


def capture_once(
    hwnd: int,
    *,
    capture_factory=None,
    area_locator=None,
    clock=time.monotonic,
    sleep=time.sleep,
    timeout: float = 2.0,
    poll: float = 0.05,
) -> tuple[object, tuple[int, ...]]:
    """Return one stable frame and its chat area, then always stop capture."""
    if capture_factory is None:
        from app.capture import Capture

        capture_factory = Capture
    if area_locator is None:
        from app.capture import chat_area

        area_locator = chat_area

    capture = capture_factory(hwnd)
    try:
        started = clock()
        while True:
            frame = capture.settled()
            if frame is not None:
                area = area_locator(frame)
                if area is not None:
                    return frame, area
            if clock() - started >= timeout:
                raise CaptureTimeout("等待稳定微信画面超时")
            sleep(poll)
    finally:
        capture.stop()
