"""One local capture, reduced to immutable text before returning to the controller."""

from app.fill_target import FillTarget
from app.freshness import RawVisibleChat, make_raw_visible
from app.privacy import ChatLine


def capture_visible_chat(hwnd, *, capture_once_fn=None, reader_factory=None,
                         read_title_fn=None, capture_target_fn=None
                         ) -> tuple[RawVisibleChat, FillTarget | None]:
    # Load platform adapters only when needed, including for dependency-free tests.
    if capture_once_fn is None:
        from app.manual_capture import capture_once
        capture_once_fn = capture_once
    if reader_factory is None or read_title_fn is None:
        from app.ocr import Reader, read_title
        reader_factory = reader_factory or Reader
        read_title_fn = read_title_fn or read_title
    if capture_target_fn is None:
        from app.fill_target import capture_target
        capture_target_fn = capture_target

    frame = None
    try:
        frame, area = capture_once_fn(hwnd)
        x0, y0, x1, y1, background, header_top = area
        title = read_title_fn(frame[header_top:y0, x0:x1])
        rows = reader_factory().read(frame[y0:y1, x0:x1], background)
        lines = tuple(ChatLine(who, text, name) for who, name, text, _y in rows)
        raw = make_raw_visible(title, lines, (x0, y0, x1, y1))
        target = capture_target_fn(hwnd, raw.area, title) if title.strip() else None
        return raw, target
    finally:
        frame = None
