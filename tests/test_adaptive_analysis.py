from copy import deepcopy
import unittest

from core.adaptive_analysis import (
    AdaptiveAnalysisResult,
    TurnDecision,
    TurnTarget,
    build_adaptive_questions,
    build_adaptive_state,
    normalize_messages,
    parse_adaptive_result,
    select_turn_targets,
)


class AdaptiveTargetTests(unittest.TestCase):
    def test_rank_question_supports_three_four_and_five_candidates(self):
        from core.questions import build_rank_question

        for count in (3, 4, 5):
            candidates = [f"候选{i}" for i in range(count)]
            question = build_rank_question(candidates)["best_reply"]
            self.assertEqual(
                tuple(question["criteria"]),
                ("reply_a", "reply_b", "reply_c", "reply_d", "reply_e")[:count],
            )
            self.assertEqual(list(question["criteria"].values()), candidates)

    def test_rank_question_uses_analysis_mode_without_reordering_candidates(self):
        candidates = ["继续聊聊", "我再想想", "周末你有空吗"]
        expected = {
            "reply_a": "继续聊聊",
            "reply_b": "我再想想",
            "reply_c": "周末你有空吗",
        }
        reply = build_adaptive_questions((), candidates, mode="reply")["best_reply"]
        follow_up = build_adaptive_questions((), candidates, mode="follow_up")["best_reply"]

        self.assertEqual(reply["criteria"], expected)
        self.assertEqual(follow_up["criteria"], expected)
        self.assertIn("the other person's true need", reply["instructions"])
        self.assertIn("best action type", reply["instructions"])
        self.assertIn("continues the user's latest message", follow_up["instructions"])
        self.assertIn("repetitive", follow_up["instructions"])
        self.assertIn("pressuring", follow_up["instructions"])
        self.assertIn("invented facts", follow_up["instructions"])
        self.assertIn("pretend the other person replied", follow_up["instructions"])
        self.assertNotIn("the other person's true need", follow_up["instructions"])

    def test_adaptive_questions_reject_more_than_five_candidates(self):
        targets = select_turn_targets(normalize_messages([("her", "在吗")]))
        with self.assertRaisesRegex(ValueError, "five candidates"):
            build_adaptive_questions(targets, [str(i) for i in range(6)])

    def test_state_keeps_all_visible_messages_and_questions_scope_each_prefix(self):
        messages = normalize_messages([("her", "第一句"), ("me", "收到"), ("her", "第三句")])
        targets = select_turn_targets(messages)
        state = build_adaptive_state(messages, "朋友", targets, None)
        questions = build_adaptive_questions(targets, ["候选甲", "候选乙"])

        self.assertEqual([row["text"] for row in state["chat"]["messages"]], ["第一句", "收到", "第三句"])
        self.assertIn("turn_000__true_intent", questions)
        self.assertIn("turn_002__tension_resolved", questions)
        self.assertIn("Ignore every message after `turn_000`", questions["turn_000__true_intent"]["instructions"])
        self.assertIn("best_reply", questions)
        self.assertIn("reply_a__safe_to_send", questions)
        self.assertIn("reply_worthwhile", questions)
        self.assertNotIn("acceptable_reply", questions)

    def test_follow_up_state_and_reply_worthwhile_question_include_mode_and_goal(self):
        messages = normalize_messages([("her", "你也去吗"), ("me", "我还没决定")])
        targets = select_turn_targets(messages)
        state = build_adaptive_state(
            messages, "朋友", targets, mode="follow_up", reply_goal="问周末有没有空",
        )
        questions = build_adaptive_questions(targets, ["那你周末有空吗"], mode="follow_up")

        self.assertEqual(state["chat"]["analysis_mode"], "follow_up")
        self.assertEqual(state["chat"]["reply_goal"], "问周末有没有空")
        self.assertIn("follow-up", questions["reply_worthwhile"]["instructions"])
        self.assertIn("must not assume how long", questions["reply_worthwhile"]["instructions"])

    def test_reply_state_omits_blank_goal_and_keeps_worthwhile_wording(self):
        messages = normalize_messages([("her", "你也去吗")])
        targets = select_turn_targets(messages)
        state = build_adaptive_state(messages, "朋友", targets, mode="reply", reply_goal="  ")
        questions = build_adaptive_questions(targets, ["我应该会去"], mode="reply")

        self.assertEqual(state["chat"]["analysis_mode"], "reply")
        self.assertNotIn("reply_goal", state["chat"])
        self.assertIn("sending one response now", questions["reply_worthwhile"]["instructions"])

    def test_style_is_available_to_jev_state_and_ranking(self):
        messages = normalize_messages([("her", "周末有空吗？")])
        state = build_adaptive_state(messages, "朋友", select_turn_targets(messages), style="简短直接")
        questions = build_adaptive_questions((), ["有空呀", "我看看"], style="简短直接")
        self.assertEqual(state["chat"]["style"], "简短直接")
        self.assertIn("简短直接", questions["best_reply"]["instructions"])

    def test_candidate_safety_and_reply_worthwhile_are_separate_questions(self):
        candidates = ["我理解你的感受", "我今晚一定解决"]
        reply = build_adaptive_questions((), candidates, mode="reply")
        follow_up = build_adaptive_questions((), candidates, mode="follow_up")

        self.assertEqual(
            set(reply),
            {"best_reply", "reply_a__safe_to_send", "reply_b__safe_to_send",
             "reply_worthwhile"},
        )
        self.assertIn(candidates[0], reply["reply_a__safe_to_send"]["instructions"])
        self.assertNotIn(candidates[1], reply["reply_a__safe_to_send"]["instructions"])
        self.assertIn("must not be false merely because replying is unnecessary",
                      reply["reply_a__safe_to_send"]["instructions"])
        self.assertIn("sending one response now", reply["reply_worthwhile"]["instructions"])
        self.assertIn("follow-up", follow_up["reply_worthwhile"]["instructions"])
        self.assertIn("must not assume how long", follow_up["reply_worthwhile"]["instructions"])

    def test_mode_aware_builders_reject_unknown_mode(self):
        messages = normalize_messages([("her", "你好")])
        targets = select_turn_targets(messages)
        with self.assertRaisesRegex(ValueError, "analysis mode"):
            build_adaptive_state(messages, "朋友", targets, mode="unknown")
        with self.assertRaisesRegex(ValueError, "analysis mode"):
            build_adaptive_questions(targets, ["你好"], mode="unknown")

    def test_all_messages_are_normalized_but_only_latest_ten_incoming_are_targets(self):
        source = [("her" if index % 2 == 0 else "me", f"消息{index}") for index in range(24)]
        messages = normalize_messages(source)
        targets = select_turn_targets(messages, limit=10)

        self.assertEqual(len(messages), 24)
        self.assertEqual(len(targets), 10)
        self.assertEqual([target.message_index for target in targets], list(range(4, 24, 2)))
        self.assertEqual(targets[-1].turn_id, "turn_022")
        self.assertEqual(targets[-1].prefix_end, 23)

    def test_target_selection_rejects_unknown_speaker(self):
        with self.assertRaisesRegex(ValueError, "her.*me"):
            normalize_messages([("other", "不能接受")])

    def test_targets_are_immutable(self):
        from dataclasses import FrozenInstanceError

        target = select_turn_targets(normalize_messages([("her", "你好")]))[0]
        with self.assertRaises(FrozenInstanceError):
            target.message_index = 3

    def test_empty_and_zero_limit_keep_normalization_semantics(self):
        self.assertEqual(select_turn_targets(normalize_messages([])), ())
        messages = normalize_messages([("her", "你好")])
        self.assertEqual(select_turn_targets(messages, limit=0), ())
        self.assertEqual(messages[0]["text"], "你好")

    def test_normalize_messages_accepts_named_tuples_and_dictionaries(self):
        messages = normalize_messages(
            [("her", "你好", "小美"), {"from": "me", "text": "在的"}]
        )

        self.assertEqual(
            messages,
            (
                {"id": "turn_000", "from": "her", "text": "你好", "name": "小美"},
                {"id": "turn_001", "from": "me", "text": "在的"},
            ),
        )

    def test_result_contracts_are_immutable(self):
        from dataclasses import FrozenInstanceError

        target = TurnTarget("turn_000", 0, 1)
        decision = TurnDecision("turn_000", 0, 0.9, "literal", (), 0.2, 0.8, "reply", (), "care", (), 0.1)
        result = AdaptiveAnalysisResult((), (decision,), (), None, (), (), None, "failed", None)

        for value, field, replacement in (
            (target, "prefix_end", 4),
            (decision, "intent", "changed"),
            (result, "draft_status", "ready"),
        ):
            with self.subTest(contract=type(value).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, field, replacement)


def _choice_answer(selected, names):
    return {
        "choice": selected,
        "probabilities": {name: 1.0 if name == selected else 0.0 for name in names},
    }


def _complete_response(turn_id, candidates=("候选",)):
    intents = ("confirm_you_care", "vent_anger", "request_action",
               "seek_explanation", "casual_chat", "close_topic")
    actions = ("check_history", "apologize", "give_commitment", "explain",
               "acknowledge", "say_less", "make_plan")
    needs = ("apology", "action", "explanation", "care", "nothing")
    response = {"answers": {
        f"{turn_id}__literal_question": {"noul": 0.2},
        f"{turn_id}__true_intent": _choice_answer("request_action", intents),
        f"{turn_id}__danger_level": {"score": 6.0},
        f"{turn_id}__should_reply_now": {"noul": 0.8},
        f"{turn_id}__best_action": _choice_answer("make_plan", actions),
        f"{turn_id}__she_needs": _choice_answer("action", needs),
        f"{turn_id}__tension_resolved": {"noul": 0.1},
    }}
    keys = ("reply_a", "reply_b", "reply_c", "reply_d", "reply_e")[:len(candidates)]
    for key in keys:
        response["answers"][f"{key}__safe_to_send"] = {"noul": 0.8}
    if candidates:
        response["answers"]["reply_worthwhile"] = {"noul": 0.8}
    if len(candidates) >= 2:
        response["answers"]["best_reply"] = _choice_answer(keys[-1], keys)
    return response


class AdaptiveParserTests(unittest.TestCase):
    def setUp(self):
        self.messages = normalize_messages([("her", "私密原文")])
        self.targets = select_turn_targets(self.messages)
        self.response = _complete_response(self.targets[0].turn_id)

    def parse(self, candidates=("候选",)):
        response = deepcopy(self.response)
        if not candidates:
            response["answers"].pop("reply_a__safe_to_send", None)
            response["answers"].pop("reply_worthwhile", None)
        return parse_adaptive_result(
            response, self.messages, self.targets, candidates, "ready", None,
        )

    def test_parser_maps_five_candidate_probabilities_and_winner(self):
        candidates = tuple(f"候选{i}" for i in range(5))
        response = _complete_response(self.targets[0].turn_id, candidates)
        response["answers"]["best_reply"] = {
            "choice": "reply_d",
            "probabilities": {
                "reply_a": 0.05,
                "reply_b": 0.10,
                "reply_c": 0.15,
                "reply_d": 0.60,
                "reply_e": 0.10,
            },
        }
        result = parse_adaptive_result(
            response, self.messages, self.targets, candidates, "ready", None
        )
        self.assertEqual(result.best_index, 3)
        self.assertEqual(result.reply_scores, (0.05, 0.10, 0.15, 0.60, 0.10))

    def test_parser_selects_highest_ranked_safe_candidate_when_raw_winner_is_unsafe(self):
        candidates = ("甲", "乙", "丙")
        response = _complete_response(self.targets[0].turn_id, candidates)
        response["answers"]["best_reply"] = {
            "choice": "reply_b",
            "probabilities": {"reply_a": 0.3, "reply_b": 0.5, "reply_c": 0.2},
        }
        response["answers"]["reply_b__safe_to_send"] = {"noul": 0.2}
        result = parse_adaptive_result(
            response, self.messages, self.targets, candidates, "ready", None
        )
        self.assertEqual(result.candidate_safety_probabilities, (0.8, 0.2, 0.8))
        self.assertEqual(result.reply_worthwhile_probability, 0.8)
        self.assertEqual(result.reply_scores, (0.3, 0.5, 0.2))
        self.assertEqual(result.best_index, 0)

    def test_single_safe_candidate_needs_no_rank_and_keeps_worthwhile_separate(self):
        response = _complete_response(self.targets[0].turn_id, ("甲",))
        response["answers"]["reply_worthwhile"] = {"noul": 0.2}
        result = parse_adaptive_result(
            response, self.messages, self.targets, ("甲",), "ready", None,
            reply_goal="礼貌收尾",
        )
        self.assertEqual(result.best_index, 0)
        self.assertEqual(result.reply_scores, (1.0,))
        self.assertEqual(result.reply_goal, "礼貌收尾")

    def test_parser_rejects_mismatched_safety_keys_and_invalid_probabilities(self):
        candidates = ("甲", "乙")
        for mutate in ("missing", "extra", "nan"):
            response = _complete_response(self.targets[0].turn_id, candidates)
            if mutate == "missing":
                del response["answers"]["reply_b__safe_to_send"]
            elif mutate == "extra":
                response["answers"]["reply_c__safe_to_send"] = {"noul": 0.8}
            else:
                response["answers"]["reply_a__safe_to_send"] = {"noul": float("nan")}
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                parse_adaptive_result(
                    response, self.messages, self.targets, candidates, "ready", None
                )

    def test_parser_rejects_missing_or_extra_five_way_probability_keys(self):
        candidates = tuple(f"候选{i}" for i in range(5))
        for mutate in ("missing", "extra"):
            response = _complete_response(self.targets[0].turn_id, candidates)
            probabilities = response["answers"]["best_reply"]["probabilities"]
            if mutate == "missing":
                probabilities.pop("reply_e")
            else:
                probabilities["reply_f"] = 0.0
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                parse_adaptive_result(
                    response, self.messages, self.targets, candidates, "ready", None
                )

    def test_parser_maps_every_target_and_field(self):
        result = self.parse()
        decision = result.turns[0]
        self.assertEqual(result.messages, self.messages)
        self.assertEqual(result.candidates, ("候选",))
        self.assertEqual((decision.turn_id, decision.message_index), ("turn_000", 0))
        self.assertEqual((decision.literal_probability, decision.intent, decision.tension_score),
                         (0.2, "request_action", 6.0))
        self.assertEqual((decision.should_reply_probability, decision.action, decision.need,
                          decision.resolved_probability), (0.8, "make_plan", "action", 0.1))
        self.assertEqual(dict(decision.intent_probabilities)["request_action"], 1.0)
        self.assertEqual(dict(decision.action_probabilities)["make_plan"], 1.0)
        self.assertEqual(dict(decision.need_probabilities)["action"], 1.0)
        self.assertEqual((result.best_index, result.reply_scores), (0, (1.0,)))
        self.assertEqual(result.candidate_safety_probabilities, (0.8,))
        self.assertEqual(result.reply_worthwhile_probability, 0.8)

    def test_parser_preserves_analysis_mode(self):
        result = parse_adaptive_result(
            self.response, self.messages, self.targets, ("候选",), "ready", None,
            mode="follow_up",
        )
        self.assertEqual(result.mode, "follow_up")

    def test_parser_maps_each_target_to_its_own_answer(self):
        messages = normalize_messages([("her", "第一句"), ("me", "收到"), ("her", "第三句")])
        targets = select_turn_targets(messages)
        response = _complete_response("turn_000")
        later = _complete_response("turn_002")["answers"]
        later["turn_002__danger_level"]["score"] = 3.0
        response["answers"].update(later)
        del response["answers"]["reply_a__safe_to_send"]
        del response["answers"]["reply_worthwhile"]
        result = parse_adaptive_result(response, messages, targets, (), "failed", "小美")
        self.assertEqual([turn.tension_score for turn in result.turns], [6.0, 3.0])
        self.assertEqual((result.best_index, result.reply_scores,
                          result.candidate_safety_probabilities,
                          result.reply_worthwhile_probability), (None, (), (), None))
        self.assertEqual((result.draft_status, result.reply_to), ("failed", "小美"))

    def test_parser_rejects_missing_choice_probability(self):
        key = f"{self.targets[0].turn_id}__true_intent"
        del self.response["answers"][key]["probabilities"]["casual_chat"]
        with self.assertRaisesRegex(ValueError, "choice probabilities"):
            self.parse(())

    def test_invalid_probability_never_echoes_payload(self):
        key = f"{self.targets[0].turn_id}__literal_question"
        for bad in (float("nan"), -0.01, 1.01, "私密原文", True):
            with self.subTest(bad=bad):
                response = deepcopy(self.response)
                del response["answers"]["reply_a__safe_to_send"]
                del response["answers"]["reply_worthwhile"]
                response["answers"][key]["noul"] = bad
                with self.assertRaises(ValueError) as caught:
                    parse_adaptive_result(response, self.messages, self.targets, (), "ready", None)
                self.assertNotIn("私密原文", str(caught.exception))

    def test_parser_rejects_missing_target_answer(self):
        del self.response["answers"]["turn_000__tension_resolved"]
        with self.assertRaisesRegex(ValueError, "noul answer"):
            self.parse(())

    def test_parser_rejects_unknown_choice_key(self):
        self.response["answers"]["turn_000__best_action"]["choice"] = "invented"
        with self.assertRaisesRegex(ValueError, "choice answer"):
            self.parse(())

    def test_parser_rejects_danger_outside_zero_to_nine(self):
        for bad in (-0.1, 9.1, float("inf"), True, "6"):
            with self.subTest(bad=bad):
                self.response["answers"]["turn_000__danger_level"]["score"] = bad
                with self.assertRaisesRegex(ValueError, "danger score"):
                    self.parse(())

    def test_parser_rejects_invalid_best_reply(self):
        response = _complete_response(self.targets[0].turn_id, ("甲", "乙"))
        response["answers"]["best_reply"] = _choice_answer(
            "reply_c", ("reply_a", "reply_b")
        )
        with self.assertRaisesRegex(ValueError, "choice answer"):
            parse_adaptive_result(response, self.messages, self.targets, ("甲", "乙"), "ready", None)

    def test_parser_rejects_turn_id_mismatch(self):
        wrong_targets = (TurnTarget("turn_009", 0, 1),)
        with self.assertRaisesRegex(ValueError, "turn target"):
            parse_adaptive_result(self.response, self.messages, wrong_targets, (), "ready", None)

    def test_parser_rejects_answer_for_unrequested_turn_without_echoing_key(self):
        secret_key = "turn_999__danger_level_private_chat"
        self.response["answers"][secret_key] = {"score": 2.0}
        with self.assertRaisesRegex(ValueError, "answer keys") as caught:
            self.parse()
        self.assertNotIn(secret_key, str(caught.exception))

    def test_parser_rejects_reply_answer_without_candidates(self):
        with self.assertRaisesRegex(ValueError, "answer keys") as caught:
            parse_adaptive_result(
                self.response, self.messages, self.targets, (), "ready", None,
            )
        self.assertNotIn("reply_a__safe_to_send", str(caught.exception))

    def test_ranked_reply_requires_candidate_safety_and_clears_rejected_choice(self):
        response = _complete_response(self.targets[0].turn_id, ("甲", "乙"))
        response["answers"]["best_reply"] = {
            "choice": "reply_b",
            "probabilities": {"reply_a": 0.2, "reply_b": 0.8},
        }
        result = parse_adaptive_result(response, self.messages, self.targets, ("甲", "乙"), "ready", None)
        self.assertEqual((result.best_index, result.reply_scores), (1, (0.2, 0.8)))
        response["answers"]["reply_b__safe_to_send"]["noul"] = 0.64
        result = parse_adaptive_result(response, self.messages, self.targets, ("甲", "乙"), "ready", None)
        self.assertEqual((result.best_index, result.reply_scores), (0, (0.2, 0.8)))
        del response["answers"]["reply_b__safe_to_send"]
        with self.assertRaisesRegex(ValueError, "answer keys"):
            parse_adaptive_result(response, self.messages, self.targets, ("甲", "乙"), "ready", None)

    def test_single_candidate_safety_threshold(self):
        self.response["answers"]["reply_a__safe_to_send"]["noul"] = 0.65
        self.assertEqual(self.parse().best_index, 0)
        self.response["answers"]["reply_a__safe_to_send"]["noul"] = 0.649
        self.assertIsNone(self.parse().best_index)

    def test_question_builder_rejects_six_candidates(self):
        candidates = ("甲", "乙", "丙", "丁", "戊", "己")
        with self.assertRaisesRegex(ValueError, "five candidates"):
            build_adaptive_questions(self.targets, candidates)
        with self.assertRaisesRegex(ValueError, "five candidates"):
            self.parse(candidates)
