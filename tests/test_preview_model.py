import unittest

from app.preview_model import PreviewModel
from app.privacy import ChatLine


class PreviewModelTests(unittest.TestCase):
    def test_edit_delete_and_speaker_change_are_reflected_in_snapshot(self):
        model = PreviewModel("小王", [ChatLine("her", "在吗", "小王"), ChatLine("me", "嗯")])
        model.set_text(0, "今晚有空吗")
        model.set_speaker(0, "me")
        model.delete(1)
        snapshot = model.snapshot("朋友", 10, "简短")
        self.assertEqual(snapshot.messages, (ChatLine("me", "今晚有空吗", "小王"),))

    def test_append_and_snapshot_are_isolated_from_later_edits(self):
        source = [ChatLine("her", " 在吗 ")]
        model = PreviewModel(" 小王 ", source)
        source.clear()
        model.append(ChatLine("me", "有空"))
        snapshot = model.snapshot(" 朋友 ", 10, " 简短 ", " 小王 ")
        model.set_text(0, "更改")
        self.assertEqual(snapshot.messages, (ChatLine("her", "在吗"), ChatLine("me", "有空")))
        self.assertEqual((snapshot.title, snapshot.relationship, snapshot.context,
                          snapshot.style, snapshot.reply_to), ("小王", "朋友", 10, "简短", "小王"))
        self.assertIsInstance(model.lines, tuple)

    def test_redaction_can_be_undone_before_confirmation(self):
        model = PreviewModel("小王", [ChatLine("her", "电话13800138000")])
        model.apply_redaction()
        self.assertEqual(model.lines[0].text, "电话[手机号]")
        model.undo_redaction()
        self.assertEqual(model.lines[0].text, "电话13800138000")
        model.undo_redaction()
        self.assertEqual(model.lines[0].text, "电话13800138000")

    def test_redaction_includes_title_and_sender_name_sent_to_model(self):
        model = PreviewModel("联系13800138000", [ChatLine("her", "发往a@example.com", "13800138000")])
        model.apply_redaction()
        snapshot = model.snapshot("朋友", 10)
        self.assertEqual(snapshot.title, "联系[手机号]")
        self.assertEqual(snapshot.messages, (ChatLine("her", "发往[邮箱]", "[手机号]"),))
        model.undo_redaction()
        self.assertEqual(model.title, "联系13800138000")
        self.assertEqual(model.lines[0].name, "13800138000")

    def test_repeated_redaction_undo_restores_only_immediately_previous_text(self):
        model = PreviewModel("小王", [ChatLine("her", "电话13800138000")])
        model.apply_redaction()
        model.apply_redaction()
        model.undo_redaction()
        self.assertEqual(model.lines[0].text, "电话[手机号]")
        model.undo_redaction()
        self.assertEqual(model.lines[0].text, "电话[手机号]")

    def test_each_manual_operation_invalidates_redaction_undo(self):
        operations = (
            lambda model: model.set_text(0, "新的文字"),
            lambda model: model.set_speaker(0, "me"),
            lambda model: model.delete(0),
            lambda model: model.append(ChatLine("me", "补充")),
            lambda model: setattr(model, "title", "新标题"),
            lambda model: model.set_name(0, "新名字"),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                model = PreviewModel("小王", [ChatLine("her", "电话13800138000")])
                model.apply_redaction()
                operation(model)
                visible = (model.title, model.lines)
                model.undo_redaction()
                self.assertEqual((model.title, model.lines), visible)

    def test_snapshot_rejects_empty_preview_and_omits_empty_rows(self):
        model = PreviewModel("", [ChatLine("her", "  ")])
        with self.assertRaises(ValueError):
            model.snapshot("朋友", 10)
        model.append(ChatLine("me", " 留下这条 "))
        self.assertEqual(model.snapshot("朋友", 10).messages, (ChatLine("me", "留下这条"),))

    def test_invalid_speaker_is_rejected_without_changing_preview(self):
        model = PreviewModel("小王", [ChatLine("her", "你好")])
        with self.assertRaises(ValueError):
            model.set_speaker(0, "unknown")
        self.assertEqual(model.lines, (ChatLine("her", "你好"),))

    def test_preview_snapshot_forwards_reply_goal(self):
        preview = PreviewModel("会话", [ChatLine("me", "刚才那句")])
        snapshot = preview.snapshot("朋友", 1, reply_goal="补充一个问题")
        self.assertEqual(snapshot.reply_goal, "补充一个问题")


if __name__ == "__main__":
    unittest.main()
