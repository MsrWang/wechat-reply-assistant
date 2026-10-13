"""Fictional, offline native desktop render receipts; all settings isolated."""
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
from tests.test_result_view import _turn
from dataclasses import replace


def main():
    app = QApplication.instance() or QApplication([])
    for font in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    output = Path("build/ui-captures/desktop")
    output.mkdir(parents=True, exist_ok=True)
    with patch.object(settings, "load_preferences", return_value=settings.Preferences()), patch.object(settings, "migrate_legacy_preferences", return_value=False):
        ui = Overlay(lambda: None, lambda *a: None, lambda *a: None, lambda: None,
                     lambda *a: False, lambda: None, lambda: False, on_rewrite=lambda *a: None)
        def grab(name, width=1120, height=800):
            ui.win.resize(width, height)
            for _ in range(4):
                app.processEvents()
            ui.win.grab().save(str(output / (name + ".png")))
        grab("welcome")
        ui.set_preview(PreviewModel("周末安排 · 合成会话", [ChatLine("me", "周末要不要出去走走？"), ChatLine("her", "好呀，周六下午吧，我想找个安静点的地方。")]))
        ui._saved_key_present = True
        ui.set_analysis_source("live_verified")
        ui._sync_actions()
        grab("preview")
        ui.show(replace(demo_result(), turns=(_turn(1),)), allow_fill=True)
        grab("reply")
        ui.unusedToggle.setChecked(True)
        grab("alternatives")
        ui.draftEdit.setPlainText("好呀，周六下午找个安静的地方坐坐。你有想去的地方吗？")
        grab("edited")
        ui.open_settings()
        grab("settings")
        ui._back_home()
        grab("compact-reply", 820, 680)
        grab("minimum-alternatives", 720, 620)
        ui.unusedToggle.setChecked(False)
        ui.refineToggle.setChecked(True)
        grab("compact-refinement", 820, 680)
        grab("minimum-refinement", 720, 620)
        ui.refineToggle.setChecked(False)
        ui.analysisTab.click()
        grab("compact-context", 820, 680)
        ui.replyTab.click()
        ui.draftEdit.setPlainText("这是一段虚构的长回复，用于检查编辑区滚动与操作按钮。" * 150)
        grab("long-content")
        ui.show_stale_chat()
        grab("stale")
        ui.show_stage_error("Jev 判断", "虚构测试错误，请手动重试。", ["保留的虚构候选回复"])
        grab("error")
        ui.win.close()
    print(output.resolve())


if __name__ == "__main__":
    main()
