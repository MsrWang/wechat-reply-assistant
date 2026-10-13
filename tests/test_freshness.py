import unittest
from datetime import datetime, timezone

from app.freshness import make_raw_visible, same_visible_chat
from app.preview_model import PreviewModel
from app.privacy import ChatLine


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.secret = b"0123456789abcdef0123456789abcdef"
        self.area = (10, 60, 400, 500)
        self.now = datetime(2026, 9, 25, 9, 30, tzinfo=timezone.utc)

    def raw(self, lines):
        return make_raw_visible("当前聊天", lines, self.area, secret=self.secret, now=self.now)

    def test_identical_and_whitespace_only_jitter_remain_fresh(self):
        expected = self.raw([ChatLine("her", "不好意思")])
        current = self.raw([ChatLine("her", "  不好意思\n")])
        self.assertTrue(same_visible_chat(expected, current))

    def test_one_character_content_change_is_stale(self):
        expected = self.raw([ChatLine("her", "可以")])
        current = self.raw([ChatLine("her", "不可以")])
        self.assertFalse(same_visible_chat(expected, current))

    def test_new_message_area_or_title_drift_is_stale(self):
        expected = self.raw([ChatLine("her", "在吗")])
        appended = self.raw([ChatLine("her", "在吗"), ChatLine("her", "新消息")])
        self.assertFalse(same_visible_chat(expected, appended))
        self.assertFalse(same_visible_chat(
            expected,
            make_raw_visible("别的聊天", expected.lines, self.area,
                             secret=self.secret, now=self.now),
        ))

    def test_token_contains_no_plaintext(self):
        raw = self.raw([ChatLine("her", "私密正文")])
        self.assertNotIn("私密正文", raw.token.title_digest)
        self.assertNotIn("私密正文", raw.token.messages_digest)

    def test_reorder_delete_append_speaker_and_area_drift_are_stale(self):
        expected = self.raw([ChatLine("her", "甲"), ChatLine("me", "乙")])
        changed = (
            self.raw([ChatLine("me", "乙"), ChatLine("her", "甲")]),
            self.raw([ChatLine("her", "甲")]),
            self.raw([ChatLine("her", "甲"), ChatLine("me", "乙"), ChatLine("her", "丙")]),
            self.raw([ChatLine("me", "甲"), ChatLine("me", "乙")]),
            make_raw_visible("当前聊天", expected.lines, (10, 70, 400, 500),
                             secret=self.secret, now=self.now),
        )
        self.assertTrue(all(not same_visible_chat(expected, item) for item in changed))

    def test_preview_edits_do_not_mutate_raw_baseline(self):
        expected = self.raw([ChatLine("her", "OCR 原文")])
        preview = PreviewModel(expected.title, list(expected.lines))
        preview.set_text(0, "人工修正")
        self.assertEqual(expected.lines[0].text, "OCR 原文")
        self.assertTrue(same_visible_chat(expected, self.raw([ChatLine("her", "OCR 原文")])))

    def test_normalized_messages_match_exactly_and_similar_title_is_rejected(self):
        expected = self.raw([ChatLine("her", "一二三")])
        whitespace_jitter = self.raw([ChatLine("her", " 一二三 ")])
        lexical_change = self.raw([ChatLine("her", "一二四")])
        title_jitter = make_raw_visible("当前聊大", expected.lines, self.area,
                                        secret=self.secret, now=self.now)
        self.assertTrue(same_visible_chat(expected, whitespace_jitter))
        self.assertFalse(same_visible_chat(expected, lexical_change))
        self.assertFalse(same_visible_chat(expected, title_jitter))

    def test_title_only_accepts_nfc_and_whitespace_normalization(self):
        for left, right, allowed in (("project-A", "project-B", False),
                                     (" cafe\u0301  room ", "café room", True)):
            expected = make_raw_visible(left, [ChatLine("her", "甲")], self.area)
            current = make_raw_visible(right, expected.lines, self.area)
            self.assertEqual(same_visible_chat(expected, current), allowed)

    def test_read_at_is_the_injected_timezone_aware_read_time(self):
        raw = self.raw([ChatLine("her", "现在")])
        self.assertEqual(raw.read_at, self.now)
        self.assertIsNotNone(raw.read_at.tzinfo)
        self.assertIsNotNone(raw.read_at.utcoffset())


if __name__ == "__main__":
    unittest.main()
