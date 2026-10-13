import unittest

from app.privacy import ChatLine, build_snapshot, redact_text, to_engine_messages


class PrivacyTests(unittest.TestCase):
    def test_redacts_supported_patterns_without_changing_other_text(self):
        text = "电话13800138000 邮箱a.b@example.com 身份证11010519491231002X 银行卡6222020202020202020 明晚见"
        redacted, replacements = redact_text(text)
        self.assertEqual(
            redacted,
            "电话[手机号] 邮箱[邮箱] 身份证[身份证号] 银行卡[银行卡号] 明晚见",
        )
        self.assertCountEqual([item.kind for item in replacements], ["phone", "email", "id_card", "bank_card"])

    def test_build_snapshot_strips_empty_lines_and_freezes_values(self):
        source = [ChatLine("her", "  在吗  ", "小王"), ChatLine("me", "   ")]
        snapshot = build_snapshot(" 小王 ", source, "朋友", 10, "简短", "小王")
        source[0] = ChatLine("her", "被修改")
        self.assertEqual(snapshot.title, "小王")
        self.assertEqual(snapshot.messages, (ChatLine("her", "在吗", "小王"),))
        self.assertEqual(to_engine_messages(snapshot), [("her", "在吗", "小王")])

    def test_snapshot_rejects_unknown_speaker(self):
        with self.assertRaisesRegex(ValueError, "说话人"):
            build_snapshot("会话", [ChatLine("other", "你好")], "朋友", 10)

    def test_snapshot_normalizes_transient_reply_goal_without_persisting_it_elsewhere(self):
        snapshot = build_snapshot(
            "会话", [ChatLine("me", "我刚发完")], "朋友", 10,
            reply_goal="  继续聊演唱会，问周末有没有空  ",
        )
        self.assertEqual(snapshot.reply_goal, "继续聊演唱会，问周末有没有空")
        self.assertEqual(to_engine_messages(snapshot), [("me", "我刚发完", None)])

    def test_snapshot_rejects_reply_goal_over_500_characters(self):
        with self.assertRaisesRegex(ValueError, "500"):
            build_snapshot("会话", [ChatLine("her", "你好")], "朋友", 10,
                           reply_goal="目" * 501)


if __name__ == "__main__":
    unittest.main()
