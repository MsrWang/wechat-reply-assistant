"""Manual synthetic Qt render receipt. Never reads real preferences or credentials."""
import os
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from app import settings
from app.demo import demo_result
from app.overlay import Overlay
from app.preview_model import PreviewModel
from app.privacy import ChatLine


def main():
    app = QApplication.instance() or QApplication([])
    for font in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    output = Path("build/ui-captures/reply")
    output.mkdir(parents=True, exist_ok=True)
    with patch.object(settings, "load_preferences", return_value=settings.Preferences()), patch.object(settings, "migrate_legacy_preferences", return_value=False):
        ui = Overlay(lambda: None, lambda snapshot: None, lambda text: None, lambda: None,
                     lambda key: False, lambda: None, lambda: False, on_rewrite=lambda text, hint: None)
        def grab(name, width=540, height=860):
            ui.win.resize(width, height)
            app.processEvents()
            ui.win.grab().save(str(output / (name + ".png")))
        grab("first-use")
        ui.set_preview(PreviewModel("周末安排 · 合成会话", [ChatLine("me", "周末要不要出去走走？"), ChatLine("her", "好呀，周六下午吧，我想找个安静点的地方。")]))
        ui._saved_key_present = True  # Status only; no credential exists or is loaded.
        ui.set_analysis_source("live_verified")
        ui._sync_actions()
        grab("preview")
        grab("preview-narrow", 320, 640)
        ui.show(demo_result(), allow_fill=True)
        grab("reply-editor")
        ui.draftEdit.setPlainText("好呀，周六下午找个安静的地方坐坐。")
        grab("edited-wide", 780, 860)
        grab("edited-narrow", 320, 640)
        ui.show_demo()
        grab("offline-demo")
        ui.win.close()
    print(output.resolve())


if __name__ == "__main__":
    main()
