"""Exercise real UI orchestration methods, without claiming Qt rendering coverage.

Qt imports and widget endpoints are doubles. Geometry, focus, and accessibility
of rendered controls still require the separately authorized Windows gate.
"""
import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, Mock, patch

from app.preview_model import PreviewModel
from app.privacy import ChatLine
from app.result_view import ResultView, project_result, project_unreviewed_candidates
from tests.test_result_view import _result, _turn


class _QtImports(ModuleType):
    def __getattr__(self, name):
        return object if name in ("QWidget", "CardWidget", "QFrame") else MagicMock()


def load_overlay_methods():
    names = ("PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "qfluentwidgets")
    modules = {name: _QtImports(name) for name in names}
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("overlay_contract_subject", "app/overlay.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.timeline_module = sys.modules["app.conversation_timeline"]
    return module


class OverlayContractTests(unittest.TestCase):
    def setUp(self):
        self.module = load_overlay_methods()
        self.ui = self.module.Overlay.__new__(self.module.Overlay)
        ui = self.ui
        ui._busy = False
        ui._demo = False
        ui._drafts = {}
        ui._selected_index = None
        ui._previewRows = []
        ui._current = True
        ui._fill_consumed = False
        ui._saved_key_present = None
        ui._compact = False
        ui._analysis_source = "user_confirmed_only"
        ui.cands = []
        ui.cards = []
        ui._reply_views = {}
        ui.preview = PreviewModel("预览", [ChatLine("her", "未确认内容")])
        for name in ("pages", "settingsPage", "home", "centerPages", "previewScroll",
                     "focusPages", "analysisView", "replyView", "analysisTab", "replyTab",
                     "replyScroll", "welcomePanel", "welcomeSpace", "focusTabs", "chatPreferences",
                     "timeline", "replyStatus", "actionLabel", "updated", "readTimeLabel",
                     "chatTitleLabel", "targetEdit", "targetCheck", "targetRow", "titleEdit", "styleEdit",
                     "previewContext", "modeHint", "replyGoalEdit", "sourceHint", "status", "keyState",
                     "forgetKeyButton", "settingsButton", "workspaceButton", "navDemoButton", "relationshipBox", "keyEdit",
                     "reasonLabel", "fallbackLabel", "primaryReplyContent", "primaryReplyBox",
                     "otherReplyContent", "otherReplyBox", "unusedToggle", "empty", "emptyTitle",
                     "emptyHint", "previewPanel", "analyzeButton", "readButton", "setupButton", "headerPanel"):
            setattr(ui, name, Mock())
        ui.keyEdit.text.return_value = ""
        ui.styleEdit.text.return_value = ""
        ui.replyGoalEdit.text.return_value = ""
        ui.targetCheck.isChecked.return_value = False
        ui.targetEdit.text.return_value = ""
        ui._relationship = Mock(return_value="朋友")
        ui._clear_editor = Mock()
        ui.select_reply = Mock()
        ui._sync_editor = Mock()
        ui.set_busy = Mock(side_effect=lambda busy: setattr(ui, "_busy", busy))
        ui._clear_cards = Mock(side_effect=lambda: ui.cards.clear())
        ui._clear_result = Mock(side_effect=lambda: ui.cards.clear())
        ui._render_preview = Mock()
        ui._back_home = Mock()
        ui.on_key_status = Mock(return_value=True)
        ui.on_fill = Mock()
        ui.app = Mock()

    def test_focus_switch_is_local_and_never_calls_read_analyze_or_fill(self):
        self.ui.on_read = Mock()
        self.ui.on_analyze = Mock()
        self.ui.on_fill = Mock()

        self.ui._select_home_view(1)

        self.ui.focusPages.setCurrentIndex.assert_called_once_with(1)
        self.ui.analysisTab.setChecked.assert_called_once_with(False)
        self.ui.replyTab.setChecked.assert_called_once_with(True)
        self.ui.on_read.assert_not_called()
        self.ui.on_analyze.assert_not_called()
        self.ui.on_fill.assert_not_called()

    def test_reply_tab_uses_actual_candidate_count_and_accessible_name(self):
        self.ui._update_reply_tab(5)
        self.ui.replyTab.setText.assert_called_once_with("回复建议 5")
        self.ui.replyTab.setAccessibleName.assert_called_once_with("回复建议，5 条")

        self.ui.replyTab.reset_mock()
        self.ui._update_reply_tab(0)
        self.ui.replyTab.setText.assert_called_once_with("回复建议")
        self.ui.replyTab.setAccessibleName.assert_called_once_with("回复建议，尚未生成")

    def test_unknown_saved_key_allows_explicit_analysis_without_status_probe(self):
        self.assertTrue(self.ui._has_key())
        self.ui._refresh_key_state()
        self.ui.keyState.setText.assert_called_with("已保存状态将在分析或打开设置时检测")
        self.ui.on_key_status.assert_not_called()
        self.ui.open_settings()
        self.ui.on_key_status.assert_called_once_with()
        self.ui.keyState.setText.assert_called_with("已保存")
        self.ui._saved_key_present = False
        self.assertFalse(self.ui._has_key())
        self.ui.keyEdit.text.return_value = "temporary-test-value"
        self.assertTrue(self.ui._has_key())

    def test_preview_uses_capture_timestamp_and_analysis_keeps_it(self):
        captured = datetime(2026, 9, 25, 5, 12, tzinfo=timezone.utc)
        self.ui.set_preview(PreviewModel("已读取会话", []), read_at=captured)
        self.ui.chatTitleLabel.setText.assert_called_with("已读取会话")
        self.ui.readTimeLabel.setText.assert_called_once_with(captured.astimezone().strftime("%H:%M 已读取"))
        self.ui.render_result_view = Mock()
        self.ui.show(_result())
        self.ui.readTimeLabel.setText.assert_called_once()
        self.assertRegex(self.ui.updated.setText.call_args.args[0], r"^\d\d:\d\d 本次分析$")

    def test_mode_hint_and_goal_placeholder_follow_last_nonempty_speaker(self):
        self.ui.preview = PreviewModel("会话", [ChatLine("her", "你也去吗")])
        self.ui._update_analysis_guidance()
        self.ui.modeHint.setText.assert_called_with("将按回复模式分析")
        self.ui.replyGoalEdit.setPlaceholderText.assert_called_with("例如：先肯定对方，再问清楚时间")

        self.ui.preview = PreviewModel("会话", [
            ChatLine("her", "你也去吗"), ChatLine("me", "我还没决定"),
            ChatLine("her", "   "),
        ])
        self.ui._update_analysis_guidance()
        self.ui.modeHint.setText.assert_called_with("最后一句是你发出的，将按主动跟进模式分析")
        self.ui.replyGoalEdit.setPlaceholderText.assert_called_with(
            "例如：继续聊演唱会，顺便问他周末有没有空"
        )

    def test_confirmed_snapshot_contains_transient_goal(self):
        self.ui.replyGoalEdit.text.return_value = "继续聊旅行"
        snapshot = self.ui.confirmed_snapshot()
        self.assertEqual(snapshot.reply_goal, "继续聊旅行")

    def test_source_hint_distinguishes_live_and_manual_capabilities(self):
        self.ui.set_analysis_source("user_confirmed_only")
        self.ui.sourceHint.setText.assert_called_with(
            "来源：手动整理 · 仅复制"
        )
        self.ui.set_analysis_source("live_verified")
        self.ui.sourceHint.setText.assert_called_with(
            "来源：微信可见文字 · 可填入"
        )
        with self.assertRaises(ValueError):
            self.ui.set_analysis_source("unknown")

    def test_show_for_manual_source_projects_copy_only_and_mode_heading(self):
        self.ui.render_result_view = Mock()
        self.ui.show(_result(mode="follow_up"), allow_fill=False)
        view = self.ui.render_result_view.call_args.args[0]
        self.assertEqual(view.heading_text, "本轮建议：回复")
        self.assertIn("主动跟进建议无法判断你已经等待了多久。", view.status_text)
        self.assertFalse(any(reply.fill_allowed for reply in view.ordered_replies))
        self.ui.replyStatus.setText.assert_not_called()
        self.ui.status.setText.assert_called_with("·  " + view.status_text)

    def test_mark_fill_consumed_disables_fill_but_keeps_copy(self):
        card = Mock()
        self.ui.cards = [card]
        self.ui.mark_fill_consumed()
        card.set_fill_available.assert_called_once_with(False)
        card.set_available.assert_not_called()
        self.assertTrue(self.ui._current)

    def test_fill_error_preserves_current_cards_and_retry_eligibility(self):
        card = Mock()
        self.ui.cards = [card]
        self.ui._current = True
        self.ui.show_fill_error("请确认微信窗口可用后重试，或复制回复。")
        self.assertEqual(self.ui.cards, [card])
        self.assertTrue(self.ui._current)
        self.ui.status.setText.assert_called_once()

    def test_fill_callback_failure_preserves_reviewed_cards(self):
        with patch.object(self.module, "_ReplyCard", return_value=Mock()):
            self.ui.render_result_view(project_result(_result()))
        cards = list(self.ui.cards)
        self.ui.on_fill.side_effect = RuntimeError("synthetic fill failure")
        self.ui._fill(1)
        self.assertEqual(self.ui.cards, cards)
        self.assertTrue(self.ui._current)
        self.assertTrue(self.ui._reply_views[1].fill_allowed)

    def test_new_read_and_clear_discard_transient_goal(self):
        self.ui.set_preview(PreviewModel("新会话", [ChatLine("her", "你好")]))
        self.ui.replyGoalEdit.clear.assert_called_once()
        self.ui.replyGoalEdit.clear.reset_mock()
        self.ui._clear_preview_rows = Mock()
        self.ui._load_settings = Mock()
        self.ui._refresh_key_state = Mock()
        self.ui.rememberCheck = Mock()
        self.ui.settingsFeedback = Mock()
        with patch.object(self.module, "QLineEdit", Mock(Password="password")):
            self.ui.clear_chat_fields()
        self.ui.replyGoalEdit.clear.assert_called_once()
        self.assertEqual(self.ui._analysis_source, "user_confirmed_only")

    def test_append_action_copy_is_explicit_in_source(self):
        overlay_source = Path("app/overlay.py").read_text(encoding="utf-8")
        self.assertIn("填入微信（追加）", overlay_source)
        self.assertIn("会追加到微信当前输入框末尾；如果已有草稿，请先在微信中整理。", overlay_source)

    def test_preview_context_exposes_relationship_style_count_and_chat_kind(self):
        self.ui.preview = PreviewModel("会话", [
            ChatLine("her", "第一条"),
            ChatLine("me", "第二条"),
        ])
        self.ui._relationship = Mock(return_value="朋友")
        self.ui.styleEdit.text.return_value = "简短，不用句号"
        self.assertEqual(
            self.ui._preview_context_text(),
            "关系：朋友｜口吻：简短，不用句号｜可见消息：2 条｜单聊",
        )

    def test_long_context_summary_is_bounded_while_full_text_stays_accessible(self):
        self.ui._relationship = Mock(return_value="关" * 500)
        self.ui.styleEdit.text.return_value = "口" * 500

        self.ui._preview_changed()

        self.ui.previewContext.setText.assert_called_once_with(
            "关系：" + "关" * 12 + "…｜口吻：" + "口" * 12
            + "…｜可见消息：1 条｜单聊"
        )
        full = "关系：" + "关" * 500 + "｜口吻：" + "口" * 500 + "｜可见消息：1 条｜单聊"
        self.ui.previewContext.setToolTip.assert_called_once_with(full)
        self.ui.previewContext.setAccessibleDescription.assert_called_once_with(full)

    def test_preview_context_marks_group_and_reply_target(self):
        self.ui.preview = PreviewModel("群聊", [ChatLine("her", "第一条", "小王")])
        self.ui._relationship = Mock(return_value="同事")
        self.ui.targetCheck.isChecked.return_value = True
        self.ui.targetEdit.text.return_value = "小王"
        self.assertEqual(
            self.ui._preview_context_text(),
            "关系：同事｜口吻：未指定｜可见消息：1 条｜群聊｜回复对象：小王",
        )

    def test_preview_context_refreshes_after_preview_edit_and_render(self):
        self.ui._preview_changed()
        self.ui.previewContext.setText.assert_called_with(
            "关系：朋友｜口吻：未指定｜可见消息：1 条｜单聊"
        )
        self.ui.previewContext.reset_mock()
        self.ui.preview = PreviewModel("会话", [])
        self.ui._previewRows = []
        self.ui._render_preview = self.module.Overlay._render_preview.__get__(self.ui)
        self.ui._render_preview()
        self.ui.previewContext.setText.assert_called_with(
            "关系：朋友｜口吻：未指定｜可见消息：0 条｜单聊"
        )

    def test_single_chat_hides_and_excludes_configured_reply_target(self):
        self.ui.preview = PreviewModel("会话", [ChatLine("her", "第一条")])
        self.ui.targetCheck.isChecked.return_value = True
        self.ui.targetEdit.text.return_value = "小王"
        self.ui.targetRow = Mock()

        self.ui._target_option_changed()
        snapshot = self.ui.confirmed_snapshot()

        self.ui.targetRow.setVisible.assert_called_with(False)
        self.assertEqual(
            self.ui._preview_context_text(),
            "关系：朋友｜口吻：未指定｜可见消息：1 条｜单聊",
        )
        self.assertIsNone(snapshot.reply_to)

    def test_preview_context_ignores_blank_named_rows_like_snapshot(self):
        self.ui.preview = PreviewModel("会话", [
            ChatLine("her", "有效消息"),
            ChatLine("her", "   ", "小王"),
        ])

        snapshot = self.ui.confirmed_snapshot()

        self.assertEqual(
            self.ui._preview_context_text(),
            "关系：朋友｜口吻：未指定｜可见消息：1 条｜单聊",
        )
        self.assertEqual(snapshot.messages, (ChatLine("her", "有效消息"),))

    def test_clear_chat_fields_explicitly_refreshes_preview_context(self):
        self.ui._clear_preview_rows = Mock()
        self.ui._load_settings = Mock()
        self.ui._clear_result = Mock()
        self.ui._refresh_key_state = Mock()
        self.ui._back_home = Mock()
        self.ui.set_status = Mock()
        self.ui.rememberCheck = Mock()
        self.ui.settingsFeedback = Mock()
        self.ui.previewContext.reset_mock()

        with patch.object(self.module, "QLineEdit", Mock(Password="password")):
            self.ui.clear_chat_fields()

        self.ui.previewContext.setText.assert_called_with(
            "关系：朋友｜口吻：未指定｜可见消息：0 条｜单聊"
        )

    def test_typed_result_hands_confirmed_messages_and_projected_cards_to_timeline(self):
        result = _result(tuple(_turn(i) for i in range(12)))
        self.ui.render_result_view = Mock()
        self.ui.show(result)
        lines, view = self.ui.timeline.set_conversation.call_args.args
        self.assertEqual(len(lines), 12)
        self.assertEqual(lines[0], ChatLine("her", "消息0"))
        self.assertIsInstance(view, ResultView)
        self.assertEqual([row.message_index for row in view.turn_cards], list(range(2, 12)))
        self.ui.render_result_view.assert_called_once_with(view)
        self.ui.centerPages.setCurrentWidget.assert_called_with(self.ui.timeline)

    def test_success_selects_replies_while_clear_returns_to_analysis(self):
        self.ui._select_home_view = Mock()
        self.ui._update_reply_tab = Mock()
        self.ui.render_result_view = Mock()
        self.ui.show(_result(candidates=("一", "二", "三"), reply_scores=(0.8, 0.1, 0.1)))
        self.ui._select_home_view.assert_called_with(1)

        self.ui._select_home_view.reset_mock()
        self.ui._update_reply_tab.reset_mock()
        self.ui._clear_result = self.module.Overlay._clear_result.__get__(self.ui)
        self.ui._clear_result()
        self.ui._update_reply_tab.assert_called_once_with(0)
        self.ui._select_home_view.assert_called_once_with(0)

    def test_render_result_updates_count_without_calling_analysis(self):
        self.ui.on_analyze = Mock()
        self.ui._update_reply_tab = Mock()
        view = project_result(_result(candidates=("一", "二", "三", "四", "五"),
                                      reply_scores=(0.4, 0.2, 0.15, 0.15, 0.1),
                                      best_index=0))
        with patch.object(self.module, "_ReplyCard", return_value=Mock()):
            self.ui.render_result_view(view)
        self.ui._update_reply_tab.assert_called_once_with(5)
        self.ui.on_analyze.assert_not_called()

    def test_error_and_stale_views_follow_retained_candidate_presence(self):
        self.ui._select_home_view = Mock()
        self.ui.render_result_view = Mock()
        self.ui.show_stage_error("Jev 判断", "失败", ["旧候选"])
        self.ui._select_home_view.assert_called_with(1)

        self.ui._select_home_view.reset_mock()
        self.ui.show_stage_error("读取", "失败")
        self.ui._select_home_view.assert_called_with(0)

        self.ui._select_home_view.reset_mock()
        self.ui.cands = ["旧候选"]
        self.ui.show_stale_chat()
        self.ui._select_home_view.assert_called_with(1)

    def test_waiting_and_clearing_return_to_analysis(self):
        self.ui._select_home_view = Mock()
        self.ui.show_waiting_for_reply()
        self.ui._select_home_view.assert_called_with(0)

        self.ui._select_home_view.reset_mock()
        self.ui._clear_preview_rows = Mock()
        self.ui._load_settings = Mock()
        self.ui._refresh_key_state = Mock()
        self.ui._back_home = Mock()
        self.ui.set_status = Mock()
        self.ui.rememberCheck = Mock()
        self.ui.settingsFeedback = Mock()
        with patch.object(self.module, "QLineEdit", Mock(Password="password")):
            self.ui.clear_chat_fields()
        self.ui._select_home_view.assert_called_with(0)

    def test_reply_actions_obey_per_reply_projection_even_when_current(self):
        view = project_result(_result(candidate_safety_probabilities=(0.2, 0.8)))
        self.ui.cands = ["回复甲", "回复乙"]
        self.ui._reply_views = {reply.source_index: reply for reply in view.ordered_replies}
        self.ui._fill(0)
        self.ui.on_fill.assert_not_called()
        self.ui._fill(1)
        self.ui.on_fill.assert_called_once_with("回复乙")
        self.ui._copy(0)
        self.ui.app.clipboard().setText.assert_called_once_with("回复甲")

    def test_reply_card_busy_toggle_never_promotes_copy_only_to_fill(self):
        card = self.module._ReplyCard.__new__(self.module._ReplyCard)
        card.reply = project_unreviewed_candidates(["候选"] ).ordered_replies[0]
        card.fillButton, card.copyButton, card.selectButton = Mock(), Mock(), Mock()
        card.set_available(False)
        card.set_available(True)
        card.fillButton.setEnabled.assert_called_with(False)
        card.copyButton.setEnabled.assert_called_with(True)

    def test_jev_failure_projects_copy_only_instead_of_legacy_fill_enabled_cards(self):
        self.ui.render_result_view = Mock()
        with patch.object(self.module, "_ReplyCard", return_value=Mock()):
            self.ui.show_stage_error("Jev 判断", "请手动重试", ["候选"])
        self.ui.render_result_view.assert_called_once()
        view = self.ui.render_result_view.call_args.args[0]
        self.assertEqual(view.status_text, "生成 1 条，Jev 判断未完成，候选仅供复制")
        self.assertTrue(view.ordered_replies[0].copy_allowed)
        self.assertFalse(view.ordered_replies[0].fill_allowed)
        self.assertFalse(view.ordered_replies[0].recommended)

    def test_jev_failure_shows_retained_count_and_copy_only_in_reply_status(self):
        with patch.object(self.module, "_ReplyCard", return_value=Mock()):
            self.ui.show_stage_error("Jev 判断", "请手动重试", ["候选一", "候选二"])
        self.ui.replyStatus.setText.assert_called_with("生成 2 条，Jev 判断未完成，候选仅供复制")
        self.assertEqual([row.text for row in self.ui._reply_views.values()], ["候选一", "候选二"])
        self.assertTrue(all(row.copy_allowed and not row.fill_allowed
                            for row in self.ui._reply_views.values()))
        self.assertFalse(any(row.recommended for row in self.ui._reply_views.values()))

    def test_fill_failure_keeps_copy_only_without_claiming_judgment_failed(self):
        with patch.object(self.module, "_ReplyCard", return_value=Mock()):
            self.ui.show_stage_error("填入", "请手动复制", ["候选"])
        self.ui.replyStatus.setText.assert_called_with("填入未完成，候选仅供复制")
        self.ui.actionLabel.clear.assert_called_once()
        self.ui._fill(0)
        self.ui.on_fill.assert_not_called()
        self.ui._copy(0)
        self.ui.app.clipboard().setText.assert_called_once_with("候选")

    def test_stale_retains_copy_only_and_waiting_removes_previous_replies(self):
        self.ui.cands = ["旧候选"]
        self.ui.render_result_view = Mock()
        self.ui.show_stale_chat()
        view = self.ui.render_result_view.call_args.args[0]
        self.assertEqual(view.ordered_replies[0].text, "旧候选")
        self.assertFalse(view.ordered_replies[0].fill_allowed)
        self.assertFalse(view.ordered_replies[0].recommended)
        self.ui.actionLabel.clear.assert_called_once()
        self.assertIn("聊天已有变化，请重新读取后分析。", self.ui.status.setText.call_args.args[0])
        self.ui.show_waiting_for_reply()
        self.assertEqual(self.ui.cands, [])
        self.assertIn("已读取到你刚发出的消息，等待对方回复。", self.ui.status.setText.call_args.args[0])

    def test_stale_invalidated_reply_can_be_copied_but_cannot_fill(self):
        with patch.object(self.module, "_ReplyCard", return_value=Mock()):
            self.ui.render_result_view(project_result(_result()))
            self.ui.invalidate_replies()
            self.ui.show_stale_chat()
        self.ui._fill(1)
        self.ui.on_fill.assert_not_called()
        self.ui._copy(1)
        self.ui.app.clipboard().setText.assert_called_once_with("回复乙")
        self.assertTrue(all(row.copy_allowed and not row.fill_allowed
                            for row in self.ui._reply_views.values()))

    def test_render_result_view_collapses_rejected_candidates_and_preserves_flags(self):
        view = project_result(_result(best_index=None, candidate_safety_probabilities=(0.3, 0.3)))
        with patch.object(self.module, "_ReplyCard", side_effect=lambda *a, **k: Mock()) as cards:
            self.ui.render_result_view(view)
        self.ui.replyStatus.setText.assert_called_with("本轮候选质量不足")
        self.ui.unusedToggle.setVisible.assert_called_with(False)
        self.ui.unusedToggle.setChecked.assert_called_with(False)
        self.ui.primaryReplyContent.hide.assert_called()
        self.ui.otherReplyContent.setVisible.assert_called_with(True)
        self.assertEqual([call.args[1] for call in cards.call_args_list], list(view.ordered_replies))

    def test_render_result_view_keeps_five_candidates_and_updates_rejection(self):
        result = _result(
            candidates=("一", "二", "三", "四", "五"),
            reply_scores=(0.1, 0.1, 0.6, 0.1, 0.1),
            candidate_safety_probabilities=(0.8,) * 5,
            best_index=2,
        )
        created = []

        def card_factory(owner, reply, number):
            card = Mock()
            card.reply = reply
            created.append(card)
            return card

        with patch.object(self.module, "_ReplyCard", side_effect=card_factory):
            self.ui.render_result_view(project_result(result))
            self.assertEqual(len(created), 5)
            self.ui.select_reply.assert_called_with(2)
            self.ui.otherReplyContent.setVisible.assert_called_with(True)
            self.assertEqual(sum(card.reply.fill_allowed for card in created), 5)
            rejected = _result(
                candidates=("一", "二", "三", "四", "五"),
                reply_scores=(0.1, 0.1, 0.6, 0.1, 0.1),
                best_index=None,
                candidate_safety_probabilities=(0.3,) * 5,
            )
            self.ui.render_result_view(project_result(rejected))
        self.ui.unusedToggle.setVisible.assert_called_with(False)
        self.ui.unusedToggle.setChecked.assert_called_with(False)
        self.ui.primaryReplyContent.hide.assert_called()
        self.ui.otherReplyContent.setVisible.assert_called_with(True)
        self.assertFalse(any(card.reply.fill_allowed for card in created[-5:]))

    def test_render_result_view_shows_draft_format_status_without_candidates(self):
        view = project_result(_result(
            candidates=(), best_index=None, reply_scores=(),
            candidate_safety_probabilities=(), reply_worthwhile_probability=None,
            draft_status="invalid_response",
        ))
        self.ui.render_result_view(view)
        self.ui.replyStatus.setText.assert_called_with("回复建议")
        self.ui.unusedToggle.setVisible.assert_called_with(False)
        self.ui.primaryReplyContent.hide.assert_called()
        self.ui.otherReplyContent.setVisible.assert_called_with(False)

    def test_pause_view_shows_one_fallback_and_collapses_other_candidates(self):
        view = project_result(_result(reply_worthwhile_probability=0.2))
        created = []

        def card_factory(owner, reply, number):
            card = Mock()
            card.reply = reply
            created.append(card)
            return card

        with patch.object(self.module, "_ReplyCard", side_effect=card_factory):
            self.ui.render_result_view(view)

        self.ui.replyStatus.setText.assert_called_with("本轮建议：先停在这里")
        self.ui.fallbackLabel.setText.assert_called_with("如果你仍想礼貌回应")
        self.ui.reasonLabel.setText.assert_called_with("继续回复的必要性较低")
        self.ui.select_reply.assert_called_with(1)
        self.assertEqual(self.ui.otherReplyBox.addWidget.call_count, len(created))
        self.ui.otherReplyContent.setVisible.assert_called_with(True)
        self.ui.unusedToggle.setText.assert_called_with("查看其他备选")
        self.assertEqual(sum(card.reply.fill_allowed for card in created), 2)

    def test_quality_insufficient_collapses_every_candidate_for_copy_only(self):
        view = project_result(_result(
            best_index=None,
            candidate_safety_probabilities=(0.2, 0.4),
        ))
        with patch.object(self.module, "_ReplyCard", return_value=Mock()):
            self.ui.render_result_view(view)
        self.ui.primaryReplyContent.hide.assert_called()
        self.ui.unusedToggle.setText.assert_called_with("查看未采用候选（仅复制）")
        self.assertFalse(any(row.fill_allowed for row in self.ui._reply_views.values()))

    def test_other_reply_toggle_changes_copy_and_accessible_text(self):
        self.ui.on_analyze = Mock()
        replies = {row.source_index: row for row in project_result(_result()).ordered_replies}
        self.ui._reply_views = replies
        self.ui._other_toggle_open_text = "查看其他备选"
        self.ui._other_toggle_close_text = "收起其他备选"
        self.ui._set_other_replies_visible(True)
        self.ui.otherReplyContent.setVisible.assert_called_with(True)
        self.ui.unusedToggle.setVisible.assert_not_called()
        self.ui._set_other_replies_visible(False)
        self.ui.otherReplyContent.setVisible.assert_called_with(True)
        self.ui.unusedToggle.setText.assert_called_with("查看其他备选")
        self.ui.unusedToggle.setAccessibleName.assert_called_with("查看其他备选")
        self.assertIs(self.ui._reply_views, replies)
        self.ui.on_analyze.assert_not_called()

    def test_snapshot_uses_all_lines_without_obsolete_context_control(self):
        self.ui.preview = PreviewModel("会话", [ChatLine("her", f"消息{i}") for i in range(14)])
        self.ui.styleEdit, self.ui.targetCheck = Mock(), Mock()
        self.ui.styleEdit.text.return_value = "简短"
        self.ui.targetCheck.isChecked.return_value = False
        snapshot = self.ui.confirmed_snapshot()
        self.assertEqual(len(snapshot.messages), 14)
        self.assertEqual(snapshot.context, 14)

    def test_unknown_saved_state_does_not_overwrite_without_replace_confirmation(self):
        ui = self.ui
        ui._compatibility_context = 8
        ui.styleEdit, ui.targetCheck, ui.rememberCheck = Mock(), Mock(), Mock()
        ui.styleEdit.text.return_value = "简短"
        ui.targetCheck.isChecked.return_value = False
        ui.rememberCheck.isChecked.return_value = True
        ui.keyEdit.text.return_value = "synthetic-replacement"
        ui.on_save_key = Mock(return_value=True)
        ui.clear_key_fields = Mock()
        ui._settings_feedback = Mock()
        ui.win = Mock()
        dialog = Mock()
        dialog.exec.return_value = 2
        with patch.object(self.module.settings, "save_preferences"), patch.object(
            self.module, "QMessageBox", Mock(return_value=dialog, Yes=1, Cancel=2)
        ):
            ui._save()
        ui.on_save_key.assert_not_called()
        dialog.exec.assert_called_once()

    def test_timeline_interleaves_all_messages_with_only_projected_latest_cards(self):
        timeline_module = self.module.timeline_module

        timeline = timeline_module.ConversationTimeline.__new__(timeline_module.ConversationTimeline)
        timeline.clear, timeline.rows, timeline._scroll_to_bottom = Mock(), Mock(), Mock()
        result = _result(tuple(_turn(i) for i in range(12)))
        lines = tuple(ChatLine("her", f"消息{i}") for i in range(12))
        with patch.object(timeline_module, "MessageBubble", side_effect=lambda line, mine: ("message", line.text)), patch.object(
            timeline_module, "JevAnalysisCard", side_effect=lambda card: ("card", card.message_index)
        ):
            timeline.set_conversation(lines, project_result(result))
        rendered = [call.args[0] for call in timeline.rows.addWidget.call_args_list]
        self.assertEqual(rendered[:4], [("message", "消息0"), ("message", "消息1"),
                                        ("message", "消息2"), ("card", 2)])
        self.assertEqual([row[1] for row in rendered if row[0] == "card"], list(range(2, 12)))
        self.assertEqual(len([row for row in rendered if row[0] == "message"]), 12)


if __name__ == "__main__":
    unittest.main()
