import unittest

from app.privacy import ChatLine, build_snapshot
from app.session import SessionState
from app.fill_target import FillTarget
from app.freshness import make_raw_visible
from core.adaptive_analysis import TurnTarget, parse_adaptive_result
from tests.test_engine import jev_response


class SessionLifecycleTests(unittest.TestCase):
    def test_clear_chat_resets_source_and_fill_consumption(self):
        session = SessionState(
            analysis_source="live_verified",
            fill_consumed=True,
            fill_target=object(),
            raw_visible=object(),
        )
        session.clear_chat()
        self.assertEqual(session.analysis_source, "user_confirmed_only")
        self.assertFalse(session.fill_consumed)
        self.assertFalse(session.can_fill())

    def test_can_fill_requires_live_source_target_raw_result_and_unconsumed_state(self):
        session = SessionState(
            analysis_source="live_verified",
            fill_target=object(), raw_visible=object(), result=object(),
        )
        self.assertTrue(session.can_fill())
        session.fill_consumed = True
        self.assertFalse(session.can_fill())

    def test_analysis_requires_key_and_confirmed_snapshot(self):
        session = SessionState()
        self.assertFalse(session.can_analyze(False))
        self.assertFalse(session.can_analyze(True))
        session.confirmed = build_snapshot("小王", [ChatLine("her", "在吗")], "朋友", 10)
        self.assertFalse(session.can_analyze(False))
        self.assertTrue(session.can_analyze(True))

    def test_clear_chat_drops_chat_state_without_owning_a_key(self):
        target = FillTarget(7, 88, "weixin.exe", (1, 2, 3, 4), (900, 700), 120, "小王")
        session = SessionState(frame=object(), fill_target=target)
        session.preview_title = "小王"
        session.preview_lines = [ChatLine("her", "在吗")]
        session.confirmed = build_snapshot("小王", session.preview_lines, "朋友", 10)
        session.candidates = ["在呀"]
        session.raw_visible = make_raw_visible("小王", session.preview_lines, target.area)
        session.result = parse_adaptive_result(
            jev_response(candidates=("在呀",)),
            ({"id": "turn_000", "from": "her", "text": "在吗"},),
            (TurnTarget("turn_000", 0, 1),), ("在呀",), "ready", None,
        )
        session.busy_stage = "分析"
        session.clear_chat()
        self.assertFalse(hasattr(session, "api_key"))
        self.assertIsNone(session.frame)
        self.assertIsNone(session.fill_target)
        self.assertEqual(session.preview_lines, [])
        self.assertIsNone(session.confirmed)
        self.assertEqual(session.candidates, [])
        self.assertIsNone(session.result)
        self.assertIsNone(session.raw_visible)
        self.assertEqual(session.preview_title, "")
        self.assertIsNone(session.busy_stage)


if __name__ == "__main__":
    unittest.main()
