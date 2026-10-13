import json
import unittest
from unittest.mock import patch

from core.adaptive_analysis import AdaptiveAnalysisResult
from core.engine import AnalysisCancelled, AnalysisStageError, analyze
from core.service_error import ServiceError
from app.result_view import project_result


REPLY_KEYS = ("reply_a", "reply_b", "reply_c", "reply_d", "reply_e")


def ranking_answer(candidates):
    keys = REPLY_KEYS[:len(candidates)]
    winner = keys[-1]
    weight = 1.0 / len(keys)
    return {
        "choice": winner,
        "probabilities": {key: weight for key in keys},
    }


def jev_response(turn_ids=("turn_000",), candidates=("在呀", "怎么啦"),
                 safety=None, worthwhile=0.8):
    """A complete synthetic answer set for the adaptive Jev contract."""
    answers = {}
    for turn_id in turn_ids:
        prefix = turn_id + "__"
        answers.update({
            prefix + "literal_question": {"noul": 0.7},
            prefix + "true_intent": {
                "choice": "casual_chat",
                "probabilities": {
                    "confirm_you_care": 0.05, "vent_anger": 0.05,
                    "request_action": 0.1, "seek_explanation": 0.05,
                    "casual_chat": 0.7, "close_topic": 0.05,
                },
            },
            prefix + "danger_level": {"score": 1.5},
            prefix + "should_reply_now": {"noul": 0.8},
            prefix + "best_action": {
                "choice": "acknowledge",
                "probabilities": {
                    "check_history": 0.05, "apologize": 0.05,
                    "give_commitment": 0.05, "explain": 0.05,
                    "acknowledge": 0.7, "say_less": 0.05, "make_plan": 0.05,
                },
            },
            prefix + "she_needs": {
                "choice": "nothing",
                "probabilities": {
                    "apology": 0.05, "action": 0.1, "explanation": 0.05,
                    "care": 0.1, "nothing": 0.7,
                },
            },
            prefix + "tension_resolved": {"noul": 0.9},
        })
    keys = REPLY_KEYS[:len(candidates)]
    checked_safety = tuple(safety or (0.8,) * len(candidates))
    if len(checked_safety) != len(candidates):
        raise ValueError("synthetic safety count must match candidates")
    for key, probability in zip(keys, checked_safety):
        answers[f"{key}__safe_to_send"] = {"noul": probability}
    if candidates:
        answers["reply_worthwhile"] = {"noul": worthwhile}
    if len(candidates) >= 2:
        answers["best_reply"] = ranking_answer(candidates)
    return {"answers": answers}


class EngineTests(unittest.TestCase):
    def test_default_and_explicit_request_timeout_reach_actual_draft_transport(self):
        from tests.test_clients import draft_response
        for supplied, expected in (({}, 60), ({"timeout": 9}, 9)):
            with self.subTest(expected=expected), patch(
                "core.draft.urllib.request.urlopen", return_value=draft_response('{"candidates":["收到"]}')
            ) as transport, patch("core.engine.ask", return_value=jev_response(candidates=("收到",))):
                result = analyze([("her", "请确认")], "同事", "synthetic-key", **supplied)
                self.assertEqual(result.candidates, ("收到",))
                self.assertEqual(transport.call_args.kwargs["timeout"], expected)

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_latest_own_message_runs_one_follow_up_draft_and_one_jev_call(self, draft, ask):
        draft.return_value = ["那你周末有空吗", "对了周末有安排吗", "周末要不要一起去"]
        ask.return_value = jev_response(("turn_000",), candidates=tuple(draft.return_value))
        result = analyze(
            [("her", "演唱会不错"), ("me", "我也有点想去")],
            "朋友", api_key="synthetic-key", reply_goal="问周末有没有空",
        )
        self.assertEqual((draft.call_count, ask.call_count), (1, 1))
        self.assertEqual(draft.call_args.kwargs["mode"], "follow_up")
        self.assertEqual(draft.call_args.kwargs["reply_goal"], "问周末有没有空")
        state, questions = ask.call_args.args
        self.assertEqual(state["chat"]["analysis_mode"], "follow_up")
        self.assertEqual(state["chat"]["reply_goal"], "问周末有没有空")
        self.assertIn("follow-up", questions["reply_worthwhile"]["instructions"])
        self.assertEqual(result.mode, "follow_up")

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_own_only_draft_failure_stops_without_empty_jev_request(self, draft, ask):
        draft.side_effect = ServiceError("draft", "network", None, True, "网络暂时不可用")
        with self.assertRaises(AnalysisStageError) as caught:
            analyze([("me", "我刚发完")], "朋友", api_key="synthetic-key")
        self.assertEqual(caught.exception.stage, "起草")
        ask.assert_not_called()

    def test_jev_safety_request_contains_each_exact_candidate_for_three_four_and_five(self):
        values = (
            '候选甲："收到"\n我会核对。', "候选乙：稍后确认。", "候选丙：请补充时间。",
            "候选丁：先核对记录。", "候选戊：今晚给你答复。",
        )
        for count in (3, 4, 5):
            candidates = values[:count]
            with self.subTest(count=count), patch(
                "core.engine.draft_candidates", return_value=list(candidates)
            ) as draft, patch(
                "core.engine.ask", return_value=jev_response(candidates=candidates)
            ) as ask:
                analyze([("her", "请确认安排")], "朋友", api_key="synthetic-key")
                self.assertEqual((draft.call_count, ask.call_count), (1, 1))
                _state, questions = ask.call_args.args
                for key, candidate in zip(REPLY_KEYS, candidates):
                    instructions = questions[f"{key}__safe_to_send"]["instructions"]
                    self.assertEqual(json.loads(instructions.split("Candidate: ", 1)[1]), candidate)
                    for other in candidates:
                        if other != candidate:
                            self.assertNotIn(other, instructions)
                self.assertEqual(list(questions["best_reply"]["criteria"].values()), list(candidates))

    def test_single_candidate_request_to_result_projection_preserves_safety_gate(self):
        candidates = ("我会先核对再确认。",)
        for probability, eligible in ((0.64, False), (0.65, True)):
            response = jev_response(candidates=candidates, safety=(probability,))
            response["answers"]["turn_000__tension_resolved"] = {"noul": 0.2}
            with self.subTest(probability=probability), patch(
                "core.engine.draft_candidates", return_value=list(candidates)
            ), patch("core.engine.ask", return_value=response) as ask:
                result = analyze([("her", "请确认安排")], "朋友", api_key="synthetic-key")
                self.assertIn(candidates[0], json.dumps(ask.call_args.args, ensure_ascii=False))
                self.assertEqual(result.best_index, 0 if eligible else None)
                reply = project_result(result).ordered_replies[0]
                self.assertEqual(reply.text, candidates[0])
                self.assertEqual((reply.recommended, reply.fill_allowed), (eligible, eligible))
                self.assertTrue(reply.copy_allowed)

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_normal_analysis_drafts_once_and_judges_all_visible_messages_once(self, draft, ask):
        draft.return_value = ["在呀", "怎么啦"]
        ask.return_value = jev_response(("turn_000", "turn_002"))
        result = analyze(
            [("her", "第一句"), ("me", "收到"), ("her", "最新一句")],
            "朋友", api_key="sk-test", context=1,
        )
        self.assertIsInstance(result, AdaptiveAnalysisResult)
        self.assertEqual(draft.call_count, 1)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(draft.call_args.kwargs["keep"], 3)
        self.assertEqual(len(ask.call_args.args[0]["chat"]["messages"]), 3)
        self.assertEqual([turn.message_index for turn in result.turns], [0, 2])
        self.assertEqual([turn.turn_id for turn in result.turns], ["turn_000", "turn_002"])
        self.assertEqual(result.best_index, 0)
        self.assertEqual(result.reply_scores, (0.5, 0.5))

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_invalid_draft_output_still_runs_one_core_jev_analysis_without_ranking(self, draft, ask):
        draft.side_effect = ServiceError("draft", "invalid_response", None, False, "返回不可用")
        ask.return_value = jev_response(candidates=())
        result = analyze([("her", "在吗")], "朋友", api_key="sk-test")
        self.assertEqual(draft.call_count, 1)
        self.assertEqual(ask.call_count, 1)
        self.assertNotIn("best_reply", ask.call_args.args[1])
        self.assertNotIn("acceptable_reply", ask.call_args.args[1])
        self.assertEqual(result.draft_status, "invalid_response")
        self.assertEqual(result.candidates, ())

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_invalid_draft_format_keeps_jev_analysis_but_reports_format_status(self, draft, ask):
        draft.side_effect = ServiceError(
            "draft", "invalid_response", None, False, "模型返回格式异常，请手动重试。"
        )
        ask.return_value = jev_response(candidates=())
        ask.return_value["answers"]["turn_000__tension_resolved"] = {"noul": 0.2}
        result = analyze([("her", "在吗")], "朋友", api_key="synthetic-key")
        self.assertEqual((draft.call_count, ask.call_count), (1, 1))
        self.assertEqual(result.draft_status, "invalid_response")
        self.assertEqual(result.candidates, ())
        self.assertNotIn("best_reply", ask.call_args.args[1])
        self.assertNotIn("acceptable_reply", ask.call_args.args[1])
        self.assertEqual(project_result(result).status_text, "未生成回复：模型返回格式异常")

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_other_draft_service_failure_reports_unavailable_status(self, draft, ask):
        draft.side_effect = ServiceError("draft", "network", None, True, "敏感错误详情")
        ask.return_value = jev_response(candidates=())
        ask.return_value["answers"]["turn_000__tension_resolved"] = {"noul": 0.2}
        result = analyze([("her", "在吗")], "朋友", api_key="synthetic-key")
        self.assertEqual((draft.call_count, ask.call_count), (1, 1))
        self.assertEqual(result.draft_status, "failed")
        self.assertEqual(project_result(result).status_text, "未生成回复：起草服务暂时不可用")
        self.assertNotIn("敏感错误详情", str(result))

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates", return_value=["在呀"])
    def test_cancellation_after_draft_prevents_jev(self, draft, ask):
        checks = iter((True, False))
        with self.assertRaises(AnalysisCancelled):
            analyze([("her", "在吗")], "朋友", api_key="sk-test",
                    is_current=lambda: next(checks))
        self.assertEqual(draft.call_count, 1)
        ask.assert_not_called()

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates", return_value=["在呀"])
    def test_freshness_callbacks_run_exactly_before_each_external_request(self, draft, ask):
        order = []

        def current():
            order.append("check")
            return True

        def draft_reply(*args, **kwargs):
            order.append("draft")
            return ["在呀"]

        def judge(*args, **kwargs):
            order.append("jev")
            return jev_response(candidates=("在呀",))

        draft.side_effect = draft_reply
        ask.side_effect = judge
        result = analyze([("her", "在吗")], "朋友", api_key="sk-test", is_current=current)
        self.assertEqual(order, ["check", "draft", "check", "jev"])
        self.assertEqual(result.candidates, ("在呀",))

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_shared_key_and_oversized_draft_failures_stop_before_jev(self, draft, ask):
        for category, status in (("auth", 401), ("balance", 402),
                                 ("permission", 403), ("input_too_large", 413)):
            with self.subTest(category=category):
                draft.side_effect = ServiceError("draft", category, status, False, "安全提示")
                with self.assertRaises(AnalysisStageError) as caught:
                    analyze([("her", "在吗")], "朋友", api_key="sk-secret")
                self.assertEqual((caught.exception.category, caught.exception.status),
                                 (category, status))
                self.assertEqual(caught.exception.user_message, "安全提示")
                self.assertNotIn("sk-secret", str(caught.exception))
                ask.assert_not_called()
        self.assertEqual(draft.call_count, 4)

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates", side_effect=RuntimeError("sk-secret raw request payload"))
    def test_unstructured_draft_failure_falls_back_without_exposing_payload(self, draft, ask):
        ask.return_value = jev_response(candidates=())
        result = analyze([("her", "在吗")], "朋友", api_key="sk-secret")
        self.assertEqual((draft.call_count, ask.call_count), (1, 1))
        self.assertEqual((result.draft_status, result.candidates), ("failed", ()))

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates", return_value=["已起草"])
    def test_jev_payment_error_preserves_candidates(self, draft, ask):
        ask.side_effect = ServiceError("jev", "balance", 402, False, "OpenRouter 余额不足，请充值后手动重试。")
        with self.assertRaises(AnalysisStageError) as caught:
            analyze([("her", "在吗")], "朋友", api_key="sk-secret")
        self.assertEqual((draft.call_count, ask.call_count), (1, 1))
        self.assertEqual(caught.exception.category, "balance")
        self.assertEqual(caught.exception.candidates, ["已起草"])

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates", return_value=["在呀", "怎么啦"])
    def test_unsafe_candidates_do_not_gain_a_default_recommendation(self, draft, ask):
        ask.return_value = jev_response(safety=(0.31, 0.31))
        result = analyze([("her", "先不用回复了")], "朋友", api_key="sk-test")
        self.assertEqual((draft.call_count, ask.call_count), (1, 1))
        self.assertEqual(result.candidate_safety_probabilities, (0.31, 0.31))
        self.assertIsNone(result.best_index)

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_pause_decision_still_uses_one_draft_and_one_jev_request(self, draft, ask):
        draft.return_value = ["我明白了", "好，那先这样"]
        ask.return_value = jev_response(candidates=tuple(draft.return_value), worthwhile=0.2)
        result = analyze(
            [("her", "嗯嗯")], "朋友", api_key="synthetic-key", reply_goal="礼貌收尾"
        )
        self.assertEqual((draft.call_count, ask.call_count), (1, 1))
        self.assertEqual(result.reply_worthwhile_probability, 0.2)
        self.assertEqual(result.reply_goal, "礼貌收尾")
        self.assertIsNotNone(result.best_index)

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_draft_failure_does_not_manufacture_an_empty_recommendation(self, draft, ask):
        draft.side_effect = ServiceError("draft", "invalid_response", None, False, "返回不可用")
        ask.return_value = jev_response(candidates=())
        result = analyze([("her", "在吗")], "朋友", api_key="sk-test")
        self.assertEqual(result.candidates, ())
        self.assertIsNone(result.best_index)
        self.assertEqual(result.reply_scores, ())

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates")
    def test_invalid_local_input_is_rejected_before_service_calls(self, draft, ask):
        for messages in ([], [("other", "在吗")]):
            with self.subTest(messages=messages):
                with self.assertRaises(ValueError):
                    analyze(messages, "朋友", api_key="sk-test")
        draft.assert_not_called()
        ask.assert_not_called()

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates", return_value=["已起草"])
    def test_malformed_jev_answers_are_safe_stage_failures_with_candidates(self, draft, ask):
        malformed = (
            ["not-a-response-object"],
            {"answers": []},
            {"answers": {"turn_000__literal_question": {"noul": float("nan")}}},
            {"answers": {"turn_000__danger_level": {"score": 10**400}}},
        )
        for response in malformed:
            with self.subTest(response_type=type(response).__name__):
                ask.return_value = response
                with self.assertRaises(AnalysisStageError) as caught:
                    analyze([("her", "在吗")], "朋友", api_key="sk-test")
                self.assertEqual(caught.exception.stage, "Jev 判断")
                self.assertEqual(caught.exception.candidates, ["已起草"])
                self.assertNotIn(repr(response), str(caught.exception))
        self.assertEqual((draft.call_count, ask.call_count), (4, 4))

    @patch("core.engine.ask")
    @patch("core.engine.draft_candidates", return_value=["在呀", "怎么啦"])
    def test_malformed_rank_probability_is_safe_jev_failure_with_candidates(self, draft, ask):
        for value in (float("nan"), "private-chat payload", 1.01, 10**400):
            with self.subTest(value_type=type(value).__name__):
                response = jev_response()
                response["answers"]["best_reply"]["probabilities"]["reply_b"] = value
                ask.return_value = response
                with self.assertRaises(AnalysisStageError) as caught:
                    analyze([("her", "在吗")], "朋友", api_key="sk-secret")
                self.assertEqual(caught.exception.stage, "Jev 判断")
                self.assertEqual(caught.exception.candidates, ["在呀", "怎么啦"])
                self.assertEqual(caught.exception.category, "invalid_response")
                self.assertNotIn(str(value), str(caught.exception))
                self.assertNotIn("sk-secret", str(caught.exception))
        self.assertEqual((draft.call_count, ask.call_count), (4, 4))
