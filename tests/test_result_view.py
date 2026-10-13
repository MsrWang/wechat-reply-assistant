from dataclasses import FrozenInstanceError, replace
import unittest

from app.result_view import project_result
from core.adaptive_analysis import AdaptiveAnalysisResult, TurnDecision, normalize_messages


def _turn(index, **changes):
    turn = TurnDecision(
        turn_id=f"turn_{index:03d}", message_index=index, literal_probability=0.8,
        intent="request_action", intent_probabilities=(("request_action", 0.8),),
        tension_score=3.0, should_reply_probability=0.8,
        action="make_plan", action_probabilities=(("make_plan", 0.8),),
        need="action", need_probabilities=(("action", 0.8),),
        resolved_probability=0.1,
    )
    return replace(turn, **changes)


def _result(turns=None, *, mode="reply", **changes):
    turns = turns if turns is not None else (_turn(0),)
    result = AdaptiveAnalysisResult(
        messages=normalize_messages([("her", f"消息{i}") for i in range(len(turns))]),
        turns=turns, candidates=("回复甲", "回复乙"), best_index=1,
        reply_scores=(0.2, 0.8), candidate_safety_probabilities=(0.8, 0.8),
        reply_worthwhile_probability=0.8, reply_goal="",
        draft_status="ready", reply_to=None,
    )
    return replace(result, mode=mode, **changes)


class AdaptiveProjectionTests(unittest.TestCase):
    def test_no_turn_evidence_has_no_placeholder_action(self):
        view = project_result(_result(turns=()))
        self.assertEqual(view.action_text, "")

    def test_follow_up_keeps_historical_card_action_without_global_action(self):
        historical = _turn(0, action="apologize")
        messages = normalize_messages([("her", "你忘记约定了"), ("me", "刚才是我的错")])
        view = project_result(_result((historical,), mode="follow_up", messages=messages))
        self.assertEqual(view.action_text, "")
        self.assertIn("最佳行动 · 为已知问题道歉", view.turn_cards[0].rows)
        reply_view = project_result(_result((historical,)))
        self.assertEqual(reply_view.action_text, "建议：为已知问题道歉")

    def test_follow_up_is_not_blocked_by_historical_stop_decision(self):
        result = _result(
            (_turn(0, resolved_probability=0.9, need="nothing", action="say_less"),),
            mode="follow_up",
        )
        view = project_result(result)
        self.assertEqual(view.heading_text, "本轮建议：回复")
        self.assertIn("主动跟进建议无法判断你已经等待了多久。", view.status_text)
        self.assertFalse(view.stop_recommended)
        self.assertTrue(view.reply_fill_allowed)
        self.assertTrue(view.ordered_replies[0].fill_allowed)

    def test_reply_mode_historical_stop_card_does_not_override_worthwhile_reply(self):
        result = _result(
            (_turn(0, tension_score=6.0), _turn(1, tension_score=1.0, resolved_probability=0.9, need="nothing", action="say_less")),
            mode="reply", reply_worthwhile_probability=0.8,
        )
        view = project_result(result)
        self.assertIn("建议结束，不必继续补充", view.turn_cards[-1].rows)
        self.assertEqual(view.reply_state, "reply")
        self.assertTrue(view.reply_fill_allowed)

    def test_follow_up_can_recommend_pause_without_using_historical_stop(self):
        result = _result(
            (_turn(0, resolved_probability=0.1, need="care", action="acknowledge"),),
            mode="follow_up", reply_worthwhile_probability=0.2,
        )
        view = project_result(result)
        self.assertEqual(view.reply_state, "pause")
        self.assertTrue(view.ordered_replies[0].fill_allowed)

    def test_manual_source_forces_copy_only_without_changing_ranking(self):
        view = project_result(_result(mode="follow_up"), allow_fill=False)
        self.assertFalse(view.reply_fill_allowed)
        self.assertTrue(all(reply.copy_allowed for reply in view.ordered_replies))
        self.assertFalse(any(reply.fill_allowed for reply in view.ordered_replies))
        self.assertEqual([reply.source_index for reply in view.ordered_replies], [1, 0])
        self.assertIn("仅可复制", view.status_text)

    def test_five_cards_show_latest_rising_tension_and_true_intent(self):
        turns = tuple(_turn(i, tension_score=float(i + 1)) for i in range(5))
        view = project_result(_result(turns))

        self.assertEqual(len(view.turn_cards), 5)
        self.assertEqual(view.turn_cards[-1].trend_text, "紧张度上升")
        self.assertEqual(view.turn_cards[-1].rows[0], "真实意图 · 希望你采取行动")
        self.assertLessEqual(len(view.turn_cards[-1].rows), 4)
        self.assertFalse(view.stop_recommended)
        self.assertTrue(view.reply_fill_allowed)
        self.assertEqual([row.source_index for row in view.ordered_replies], [1, 0])
        with self.assertRaises(FrozenInstanceError):
            view.turn_cards[-1].urgent = True

    def test_reviewed_replies_keep_explicit_best_then_descending_jev_scores(self):
        result = _result(
            candidates=("回复甲", "回复乙", "回复丙"),
            best_index=0,
            reply_scores=(0.7, 0.1, 0.2),
            candidate_safety_probabilities=(0.8, 0.8, 0.8),
        )
        view = project_result(result)

        self.assertEqual([row.source_index for row in view.ordered_replies], [0, 2, 1])
        self.assertEqual([row.percent_text for row in view.ordered_replies], ["70%", "20%", "10%"])
        self.assertEqual([row.fill_allowed for row in view.ordered_replies], [True, True, True])

        tied = project_result(replace(result, reply_scores=(0.7, 0.2, 0.2)))
        self.assertEqual([row.source_index for row in tied.ordered_replies], [0, 1, 2])

        unranked = project_result(replace(result, best_index=None))
        self.assertEqual([row.source_index for row in unranked.ordered_replies], [0, 1, 2])
        self.assertFalse(unranked.reply_fill_allowed)

        incomplete = project_result(replace(result, reply_scores=(0.7,)))
        self.assertEqual([row.source_index for row in incomplete.ordered_replies], [0, 1, 2])
        self.assertFalse(incomplete.reply_fill_allowed)

        stopped = project_result(replace(
            result,
            turns=(_turn(0, need="nothing", action="say_less"),),
            best_index=2,
        ))
        self.assertEqual([row.source_index for row in stopped.ordered_replies], [2, 0, 1])
        self.assertTrue(stopped.reply_fill_allowed)
        self.assertFalse(stopped.replies_rejected)
        self.assertEqual(sum(row.recommended and row.fill_allowed for row in stopped.ordered_replies), 1)

    def test_only_latest_ten_incoming_turns_get_cards_without_mutating_context(self):
        result = _result(tuple(_turn(i) for i in range(12)))
        view = project_result(result)

        self.assertEqual([card.message_index for card in view.turn_cards], list(range(2, 12)))
        self.assertEqual(len(result.messages), 12)
        self.assertEqual(result.messages[0]["text"], "消息0")

    def test_high_tension_surfaces_need_and_best_action(self):
        view = project_result(_result((_turn(0, tension_score=7.0, action="apologize", need="apology"),)))
        card = view.turn_cards[0]

        self.assertEqual(card.rows, (
            "真实意图 · 希望你采取行动", "紧张度 7/9",
            "对方需要 · 真诚道歉", "最佳行动 · 为已知问题道歉",
        ))
        self.assertTrue(card.urgent)

    def test_immediate_substantive_reply_surfaces_probability_and_action(self):
        view = project_result(_result((_turn(0, should_reply_probability=0.65),)))

        self.assertEqual(view.turn_cards[0].rows[2:], (
            "应该给出具体内容 · 65%", "最佳行动 · 商量具体安排",
        ))

    def test_literal_casual_talk_avoids_suggesting_subtext(self):
        view = project_result(_result((_turn(
            0, literal_probability=0.9, intent="casual_chat",
            should_reply_probability=0.2, need="care",
        ),)))

        self.assertEqual(view.turn_cards[0].rows, (
            "真实意图 · 交流与分享", "紧张度 3/9", "对方需要 · 关注与在意",
        ))
        self.assertFalse(view.stop_recommended)

    def test_never_tense_casual_sharing_does_not_claim_resolution_or_end(self):
        view = project_result(_result((_turn(
            0, literal_probability=0.95, intent="casual_chat", tension_score=0.0,
            resolved_probability=0.9, need="nothing", action="acknowledge",
            should_reply_probability=0.2,
        ),)))
        card = view.turn_cards[0]
        self.assertFalse(any("紧张已解除" in row or "建议结束" in row for row in card.rows))
        self.assertFalse(view.stop_recommended)

    def test_unresolved_subtext_appears_when_literal_probability_is_low(self):
        view = project_result(_result((_turn(
            0, literal_probability=0.2, should_reply_probability=0.2,
        ),)))

        self.assertEqual(view.turn_cards[0].rows[2], "存在潜台词 · 80%")

    def test_resolved_tension_with_no_need_keeps_historical_card_but_not_current_stop(self):
        view = project_result(_result((_turn(0, tension_score=6.0), _turn(
            1, tension_score=1.0, resolved_probability=0.65, need="nothing",
        ))))

        self.assertFalse(view.stop_recommended)
        self.assertTrue(view.reply_fill_allowed)
        self.assertEqual(view.turn_cards[-1].rows[2:], (
            "紧张已解除 · 65%", "建议结束，不必继续补充",
        ))
        self.assertEqual(sum(row.fill_allowed for row in view.ordered_replies), 2)

    def test_resolved_tension_with_remaining_need_reports_cooling(self):
        view = project_result(_result((_turn(0, tension_score=6.0), _turn(1, tension_score=1.0, resolved_probability=0.8))))

        self.assertFalse(view.stop_recommended)
        self.assertEqual(view.turn_cards[-1].rows[-1], "对话正在降温")

    def test_say_less_rupture_with_no_need_keeps_current_worthwhile_reply(self):
        view = project_result(_result((_turn(
            0, action="say_less", need="nothing", tension_score=8.0,
            resolved_probability=0.1,
        ),)))

        self.assertFalse(view.stop_recommended)
        self.assertTrue(view.reply_fill_allowed)
        self.assertTrue(view.turn_cards[0].urgent)

    def test_all_candidates_unsuitable_remain_copy_only(self):
        view = project_result(_result(best_index=None, candidate_safety_probabilities=(0.2, 0.2)))

        self.assertFalse(view.reply_fill_allowed)
        self.assertTrue(view.replies_rejected)
        self.assertTrue(all(row.copy_allowed for row in view.ordered_replies))
        self.assertFalse(any(row.fill_allowed or row.recommended for row in view.ordered_replies))
        self.assertEqual(view.status_text, "分析仍可查看，但这些候选不允许直接填入；请确认内容后重新生成。")

    def test_success_status_reports_actual_candidate_count(self):
        result = _result(
            candidates=("一", "二", "三", "四"),
            reply_scores=(0.1, 0.2, 0.6, 0.1),
            best_index=2,
            candidate_safety_probabilities=(0.8, 0.8, 0.8, 0.8),
        )
        view = project_result(result)
        self.assertEqual(view.status_text, "生成 4 条，Jev 推荐 1 条，可填入推荐回复")
        self.assertFalse(view.replies_rejected)
        self.assertEqual([row.text for row in view.ordered_replies], ["三", "二", "一", "四"])
        self.assertEqual([row.fill_allowed for row in view.ordered_replies], [True, True, True, True])

    def test_rejected_status_reports_count_and_never_allows_fill(self):
        result = _result(
            candidates=("一", "二", "三", "四", "五"),
            reply_scores=(0.2, 0.2, 0.2, 0.2, 0.2),
            best_index=None,
            candidate_safety_probabilities=(0.3,) * 5,
        )
        view = project_result(result)
        self.assertEqual(view.status_text, "分析仍可查看，但这些候选不允许直接填入；请确认内容后重新生成。")
        self.assertTrue(view.replies_rejected)
        self.assertFalse(any(row.fill_allowed or row.recommended for row in view.ordered_replies))

    def test_unranked_status_reports_count_without_fill(self):
        view = project_result(_result(best_index=None))
        self.assertEqual(view.status_text, "生成 2 条，Jev 判断未完成，候选仅供复制")
        self.assertFalse(view.replies_rejected)
        self.assertFalse(any(row.fill_allowed for row in view.ordered_replies))

    def test_draft_failure_keeps_turn_cards_without_reply(self):
        view = project_result(_result(
            candidates=(), best_index=None, reply_scores=(),
            candidate_safety_probabilities=(), reply_worthwhile_probability=None,
            draft_status="failed",
        ))

        self.assertEqual(len(view.turn_cards), 1)
        self.assertEqual(view.ordered_replies, ())
        self.assertFalse(view.replies_rejected)
        self.assertFalse(view.reply_fill_allowed)
        self.assertIn("未生成", view.status_text)

    def test_unreviewed_candidates_are_copy_only(self):
        from app.result_view import project_unreviewed_candidates

        view = project_unreviewed_candidates(("候选一", "候选二"))

        self.assertEqual(view.turn_cards, ())
        self.assertEqual([row.source_index for row in view.ordered_replies], [0, 1])
        self.assertTrue(all(row.copy_allowed for row in view.ordered_replies))
        self.assertFalse(any(row.fill_allowed or row.recommended for row in view.ordered_replies))
        self.assertFalse(view.replies_rejected)
        self.assertEqual(view.reply_state, "unreviewed")
        self.assertEqual(view.status_text, "生成 2 条，Jev 判断未完成，候选仅供复制")

    def test_single_unreviewed_candidate_retains_copy_only_expand_control(self):
        from app.result_view import project_unreviewed_candidates

        view = project_unreviewed_candidates(("候选一",))

        self.assertEqual(view.toggle_text, "查看未采用候选（仅复制）")
        self.assertEqual(view.ordered_replies[0].role, "other")
        self.assertFalse(view.ordered_replies[0].fill_allowed)

    def test_missing_rank_or_scores_never_creates_recommendation(self):
        for changes in ({"best_index": None}, {"reply_scores": ()}):
            with self.subTest(changes=changes):
                view = project_result(_result(**changes))
                self.assertFalse(view.reply_fill_allowed)
                self.assertFalse(any(row.fill_allowed or row.recommended for row in view.ordered_replies))
                if "reply_scores" not in changes:
                    self.assertEqual([row.percent_text for row in view.ordered_replies], ["20%", "80%"])
                else:
                    self.assertEqual([row.percent_text for row in view.ordered_replies], [None, None])

    def test_zero_percentages_remain_visible_without_inventing_best(self):
        view = project_result(_result(
            best_index=None, reply_scores=(0.0, 0.0),
        ))

        self.assertEqual([row.percent_text for row in view.ordered_replies], ["0%", "0%"])
        self.assertFalse(any(row.recommended or row.fill_allowed for row in view.ordered_replies))

    def test_worthwhile_safe_result_is_normal_recommendation(self):
        view = project_result(_result())
        self.assertEqual(view.reply_state, "reply")
        self.assertEqual(view.heading_text, "本轮建议：回复")
        self.assertEqual(view.ordered_replies[0].role, "recommended")
        self.assertTrue(view.ordered_replies[0].fill_allowed)
        self.assertTrue(all(row.fill_allowed for row in view.ordered_replies[1:]))

    def test_low_worthwhile_keeps_one_safe_fallback_fillable(self):
        result = _result(
            reply_worthwhile_probability=0.2,
            reply_goal="礼貌收尾",
            turns=(_turn(0, need="nothing", action="say_less"),),
        )
        view = project_result(result)
        self.assertEqual(view.reply_state, "pause")
        self.assertEqual(view.heading_text, "本轮建议：先停在这里")
        self.assertEqual(view.ordered_replies[0].role, "fallback")
        self.assertTrue(view.ordered_replies[0].fill_allowed)
        self.assertLessEqual(len(view.reason_lines), 2)
        self.assertIn("继续回复的必要性较低", view.reason_lines)
        self.assertIn("已按本次回应目的生成备选；Jev 仍更建议暂停。", view.status_text)

    def test_unsafe_raw_winner_never_gets_fill_and_safe_choice_is_first(self):
        view = project_result(_result(
            best_index=0,
            reply_scores=(0.3, 0.5),
            candidate_safety_probabilities=(0.8, 0.2),
        ))
        self.assertEqual(view.ordered_replies[0].source_index, 0)
        self.assertTrue(view.ordered_replies[0].fill_allowed)
        self.assertFalse(view.ordered_replies[1].safe_to_send)
        self.assertFalse(view.ordered_replies[1].fill_allowed)

    def test_no_safe_candidate_is_quality_insufficient_and_copy_only(self):
        view = project_result(_result(
            best_index=None,
            candidate_safety_probabilities=(0.2, 0.4),
        ))
        self.assertEqual(view.reply_state, "unsafe")
        self.assertEqual(view.heading_text, "本轮候选质量不足")
        self.assertTrue(view.replies_rejected)
        self.assertFalse(any(row.fill_allowed for row in view.ordered_replies))
        self.assertEqual(view.toggle_text, "查看未采用候选（仅复制）")

    def test_single_unsafe_candidate_retains_copy_only_expand_control(self):
        view = project_result(_result(
            candidates=("候选一",), best_index=0, reply_scores=(1.0,),
            candidate_safety_probabilities=(0.2,),
        ))

        self.assertEqual(view.reply_state, "unsafe")
        self.assertEqual(view.toggle_text, "查看未采用候选（仅复制）")
        self.assertFalse(view.ordered_replies[0].fill_allowed)

    def test_manual_pause_result_keeps_classification_but_disables_fill(self):
        view = project_result(
            _result(reply_worthwhile_probability=0.2), allow_fill=False
        )
        self.assertEqual(view.reply_state, "copy_only")
        self.assertEqual(view.ordered_replies[0].role, "fallback")
        self.assertFalse(any(row.fill_allowed for row in view.ordered_replies))
        self.assertIn("手工内容模式", view.status_text)

    def test_incomplete_safety_or_worthwhile_never_creates_fill(self):
        for changes in (
            {"candidate_safety_probabilities": ()},
            {"reply_worthwhile_probability": None},
        ):
            with self.subTest(changes=changes):
                view = project_result(_result(**changes))
                self.assertFalse(any(row.fill_allowed for row in view.ordered_replies))
                self.assertEqual(view.reply_state, "unreviewed")


if __name__ == "__main__":
    unittest.main()
