"""Offline native receipts. Never load saved preferences or capture chats."""
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
from tests.test_result_view import _result, _turn


def main():
    app = QApplication.instance() or QApplication([])
    for font in ("simhei.ttf", "msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
    output = Path("build/ui-captures/usability")
    output.mkdir(parents=True, exist_ok=True)
    with patch.object(settings, "load_preferences", return_value=settings.Preferences()), patch.object(settings, "migrate_legacy_preferences", return_value=False):
        ui = Overlay(lambda: None, lambda *a: None, lambda *a: None, lambda: None,
                     lambda *a: False, lambda: None, lambda: False, on_rewrite=lambda *a: None)

        def grab(name):
            for width, height in ((1120, 800), (720, 620)):
                ui.win.resize(width, height)
                for _ in range(4):
                    app.processEvents()
                assert ui.win.grab().save(str(output / f"{name}-{width}x{height}.png"))

        grab("welcome")
        ui.set_preview(PreviewModel("周末安排 · 合成会话", [ChatLine("me", "周末要不要出去走走？"), ChatLine("her", "好呀，周六下午吧，我想找个安静点的地方。")]))
        ui._saved_key_present = True
        ui.set_analysis_source("live_verified")
        ui._sync_actions()
        grab("preview")
        result = _result(messages=demo_result().messages, turns=(_turn(1),),
                         candidates=("好，那就周六下午，找个安静的地方坐坐。", "可以呀，你有想去的地方吗？",
                                     "周六下午可以，我们一起找个清静的地方。", "好呀，我们再商量一下去哪里。",
                                     "那就先定周六下午吧，地点慢慢选。"),
                         reply_scores=(.4, .25, .15, .12, .08),
                         candidate_safety_probabilities=(.9,) * 5, best_index=0)
        ui.show(result, allow_fill=True)
        grab("alternatives")
        ui.select_reply(1)
        ui.draftEdit.setPlainText("好呀，周六下午找个安静的地方坐坐。你有想去的地方吗？")
        grab("selected-edited")
        ui.select_reply(0)
        grab("edited-alternative")
        ui.select_reply(1)
        ui.open_settings()
        grab("settings")
        ui._back_home()
        ui.refineToggle.setChecked(True)
        grab("refinement")
        ui.refineToggle.setChecked(False)
        ui.show(_result(reply_worthwhile_probability=.2))
        grab("stop")
        ui.show(result, allow_fill=False)
        grab("copy-only")
        ui.show_stale_chat()
        grab("stale")
        ui.show_stage_error("Jev 判断", "虚构测试错误，请手动重试。", ["保留的虚构候选回复", "另一条虚构候选"])
        grab("service-error")
        ui.show_waiting_for_reply()
        grab("waiting")
        ui.show(_result(candidates=("这是一段长回复，用来核验换行与滚动。" * 35,) * 5,
                        reply_scores=(.4, .2, .2, .1, .1), candidate_safety_probabilities=(.9,) * 5, best_index=0))
        grab("long-text")
        ui.win.close()
    print(output.resolve())


if __name__ == "__main__":
    main()
