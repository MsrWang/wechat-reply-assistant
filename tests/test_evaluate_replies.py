import json
import argparse
import os
from types import SimpleNamespace
import urllib.error
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import evaluate_replies as evaluation
from scripts.synthetic_dialogues import CASES, selected_cases


class SyntheticEvaluationTests(unittest.TestCase):
    def test_only_named_fictional_cases_can_be_selected(self):
        self.assertGreaterEqual(len(CASES), 16)
        self.assertTrue(all(case["relationship"] and case["style"] and case["messages"]
                            and "goal" in case and case["reject_if"] for case in CASES.values()))
        self.assertGreaterEqual(sum(not case["goal"] and len(case["messages"]) > 1
                                    for case in CASES.values()), 4)
        self.assertEqual(len(selected_cases(["joy"])), 1)
        with self.assertRaises(ValueError):
            selected_cases(["/tmp/real-chat.json"])

    def test_budget_exhaustion_blocks_transport_before_key_loading(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            calls = []
            def transport():
                calls.append(True)
                return {"usage": {"cost": 0.1}}
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.budgeted_call(ledger, Decimal("5.01"), transport)
            self.assertEqual(calls, [])

    def test_failed_and_unknown_usage_keep_full_reservation(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            for response in (None, {"usage": {}}, {"usage": {"cost": "nan"}}):
                reservation = ledger.reserve(Decimal("0.25"))
                ledger.settle(reservation, response)
            self.assertEqual(ledger.total_usd(), Decimal("0.75"))
            self.assertEqual(json.loads((Path(directory) / "ledger.json").read_text())["cap_usd"], "5")

    def test_known_usage_only_releases_proven_unused_reserve(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            reservation = ledger.reserve(Decimal("0.4"))
            ledger.settle(reservation, {"usage": {"cost": 0.1}})
            self.assertEqual(ledger.total_usd(), Decimal("0.1"))

    def test_safe_error_has_no_key_or_body(self):
        message = evaluation.safe_error(RuntimeError("sk-secret private chat"))
        self.assertNotIn("sk-secret", message)
        self.assertNotIn("private chat", message)
        self.assertEqual(evaluation.safe_error(evaluation.EvaluationError("sk-secret private chat")), "evaluation_error")

    def test_provider_diagnostic_is_bounded_and_allowlisted(self):
        response = {
            "choices": [{
                "message": {"content": (
                    "OPENROUTER_API_KEY=sk-or-v1-secret-value "
                    + " api_key=plain-secret-value"
                    + "?token=query-secret-value"
                    + ' {"apiKey":"json-secret-value","token":"json-token-value"} '
                    + "x" * 7000
                )},
                "finish_reason": "length",
            }],
            "usage": {
                "cost": "0.12",
                "prompt_tokens": 123,
                "completion_tokens": 456,
                "total_tokens": 579,
                "reasoning_tokens": 999,
            },
            "request": {"headers": {"Authorization": "Bearer sk-secret"}},
        }
        diagnostic = evaluation._provider_diagnostic(response)
        self.assertEqual(diagnostic["finish_reason"], "length")
        self.assertEqual(len(diagnostic["content"]), 6400)
        self.assertEqual(diagnostic["usage"], {
            "cost": "0.12", "prompt_tokens": 123,
            "completion_tokens": 456, "total_tokens": 579,
        })
        self.assertNotIn("sk-secret", json.dumps(diagnostic))
        self.assertNotIn("sk-or-v1-secret-value", json.dumps(diagnostic))
        self.assertNotIn("plain-secret-value", json.dumps(diagnostic))
        self.assertNotIn("query-secret-value", json.dumps(diagnostic))
        self.assertNotIn("json-secret-value", json.dumps(diagnostic))
        self.assertNotIn("json-token-value", json.dumps(diagnostic))
        self.assertIn("[已隐藏]", diagnostic["content"])
        self.assertNotIn("headers", diagnostic)

    def test_provider_diagnostic_drops_unknown_finish_reason(self):
        diagnostic = evaluation._provider_diagnostic({
            "choices": [{"message": {"content": "safe"}, "finish_reason": "secret-token"}]
        })
        self.assertNotIn("finish_reason", diagnostic)

    def test_provider_diagnostic_drops_unbounded_or_nonfinite_usage_values(self):
        diagnostic = evaluation._provider_diagnostic({
            "usage": {
                "cost": "9" * 1000,
                "prompt_tokens": "nan",
                "completion_tokens": "-1",
                "total_tokens": True,
            }
        })
        self.assertNotIn("usage", diagnostic)

    def test_invalid_draft_response_is_saved_and_halts_without_transport_error_text(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            response = {
                "choices": [{
                    "message": {"content": "{"},
                    "finish_reason": "length",
                }],
                "usage": {"cost": "0.00001", "prompt_tokens": 10,
                          "completion_tokens": 20, "total_tokens": 30},
            }
            price = {"prompt": "0.0000001", "completion": "0.0000001"}
            with patch.object(evaluation, "_paid_request", return_value=response):
                output = evaluation._run_one(
                    ledger, "joy", CASES["joy"], "candidate", "fictional/model",
                    price, "dummy-key", False,
                )
            self.assertEqual(output["error"], "invalid_response")
            self.assertTrue(output["halted"])
            self.assertEqual(output["diagnostic"]["finish_reason"], "length")
            self.assertEqual(output["diagnostic"]["content"], "{")
            self.assertNotIn("dummy-key", json.dumps(output))
            self.assertEqual(ledger.total_usd(), Decimal("0.00001"))

    def test_invalid_jev_response_keeps_bounded_diagnostic(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            draft_response = {
                "choices": [{"message": {"content": '{"candidates":["太好了！"]}'}}],
                "usage": {"cost": "0.00001"},
            }
            jev_response = {
                "answers": {"unexpected": True},
                "choices": [{"message": {"content": "jev body"}, "finish_reason": "stop"}],
                "usage": {"cost": "0.00002"},
            }
            price = {"prompt": "0.0000001", "completion": "0.0000001"}
            with patch.object(evaluation, "_paid_request", return_value=draft_response), \
                 patch.object(evaluation, "_paid_jev_request", return_value=jev_response):
                output = evaluation._run_one(
                    ledger, "joy", CASES["joy"], "candidate", "fictional/model",
                    price, "dummy-key", True,
                )
            self.assertEqual(output["jev"]["error"], "invalid_response")
            self.assertEqual(output["jev"]["diagnostic"]["finish_reason"], "stop")
            self.assertEqual(output["jev"]["diagnostic"]["content"], "jev body")
            self.assertTrue(output["halted"])

    def test_invalid_draft_response_persists_diagnostic_and_stops_batch(self):
        with TemporaryDirectory() as directory:
            build = Path(directory)
            calls = []
            malformed = {
                "choices": [{"message": {"content": "{"}, "finish_reason": "length"}],
                "usage": {"cost": "0.00001"},
            }
            args = argparse.Namespace(case=["joy", "venting"], baseline=False, with_jev=False,
                                      candidate_model="fictional/model")
            windows_os = SimpleNamespace(name="nt", open=os.open, close=os.close, O_CREAT=os.O_CREAT,
                O_EXCL=os.O_EXCL, O_WRONLY=os.O_WRONLY, fsync=os.fsync, replace=os.replace)
            def fail_first(body, key):
                calls.append(body["model"])
                return malformed
            with patch.object(evaluation, "BUILD", build), \
                 patch.object(evaluation, "os", windows_os), \
                 patch.object(evaluation, "public_pricing", return_value={
                     "prompt": "0.0000001", "completion": "0.0000001"}), \
                 patch("app.credentials.load_saved_key", return_value="synthetic-key"), \
                 patch.object(evaluation, "_paid_request", side_effect=fail_first):
                status = evaluation._run(args)
            output = json.loads(next(build.glob("synthetic-*.json")).read_text(encoding="utf-8"))
            self.assertEqual(status, 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(output["results"]), 1)
            self.assertEqual(output["results"][0]["diagnostic"]["content"], "{")
            self.assertTrue(output["results"][0]["halted"])

    def test_actual_charge_above_reserve_is_persisted_and_blocks_next_dispatch(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.budgeted_call(ledger, Decimal("0.2"), lambda: {"usage": {"cost": "5.1"}})
            self.assertEqual(ledger.total_usd(), Decimal("5.1"))
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.budgeted_call(ledger, Decimal("0.01"), lambda: self.fail("dispatched"))

    def test_any_actual_charge_above_reserve_halts_even_below_cap(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            with self.assertRaisesRegex(evaluation.EvaluationError, "budget overrun"):
                evaluation.budgeted_call(ledger, Decimal("0.2"), lambda: {"usage": {"cost": "0.3"}})
            self.assertEqual(ledger.total_usd(), Decimal("0.3"))

    def test_baseline_builder_uses_base_source_and_base_model(self):
        case = CASES["quoted_instruction"]
        with patch.object(evaluation.draft, "_line", side_effect=AssertionError("used candidate builder")):
            request = evaluation.baseline_request(case)
        self.assertEqual(request["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(request["max_tokens"], 600)
        self.assertIn("3 至 5 条", request["messages"][1]["content"])
        self.assertIn("店员", request["messages"][1]["content"])

    def test_baseline_builder_works_without_git_history_or_git_executable(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "scripts" / "fixtures" / "draft_f8c3d66.py.txt"
            fixture.parent.mkdir(parents=True)
            fixture.write_bytes((evaluation.ROOT / "scripts" / "fixtures" /
                                 "draft_f8c3d66.py.txt").read_bytes())
            with patch.object(evaluation, "ROOT", root), patch(
                "subprocess.run", side_effect=AssertionError("git must not be required")
            ):
                request = evaluation.baseline_request(CASES["joy"])
                candidates = evaluation.baseline_candidates(
                    '{"candidates":["好呀","可以","恭喜！"]}', CASES["joy"])
            self.assertEqual(request["model"], "deepseek/deepseek-v4.1-flash")
            self.assertEqual(request["max_tokens"], 600)
            self.assertEqual(candidates, ["好呀", "可以", "恭喜！"])

    def test_missing_baseline_fixture_fails_with_safe_error(self):
        with TemporaryDirectory() as directory, patch.object(evaluation, "ROOT", Path(directory)):
            try:
                evaluation.baseline_request(CASES["joy"])
            except Exception as exc:
                self.assertIsInstance(exc, evaluation.EvaluationError)
                self.assertEqual(str(exc), "baseline_fixture_unavailable")
            else:
                self.fail("missing baseline fixture must block evaluation")

    def test_modified_baseline_fixture_cannot_silently_change_the_comparison(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "scripts" / "fixtures" / "draft_f8c3d66.py.txt"
            fixture.parent.mkdir(parents=True)
            fixture.write_bytes((evaluation.ROOT / "scripts" / "fixtures" /
                                 "draft_f8c3d66.py.txt").read_bytes() + b"\n# altered baseline\n")
            with patch.object(evaluation, "ROOT", root):
                try:
                    evaluation.baseline_request(CASES["joy"])
                except Exception as exc:
                    self.assertIsInstance(exc, evaluation.EvaluationError)
                    self.assertEqual(str(exc), "baseline_fixture_mismatch")
                else:
                    self.fail("modified baseline fixture must block evaluation")

    def test_baseline_response_uses_base_three_candidate_contract(self):
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.baseline_candidates('{"candidates":["好呀","可以"]}', CASES["joy"])
        self.assertEqual(evaluation.baseline_candidates(
            '{"candidates":["好呀","可以","恭喜！"]}', CASES["joy"]),
            ["好呀", "可以", "恭喜！"])

    def test_jev_request_uses_production_analysis_and_shared_ledger(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            captured = []
            def fake_paid(body, key):
                captured.append(body)
                return {"answers": {}, "usage": {"cost": "0.00001"}}
            with patch.object(evaluation, "_paid_jev_request", side_effect=fake_paid):
                with self.assertRaises(ValueError):
                    evaluation.run_jev(ledger, CASES["joy"], ("太好了！",), "dummy-key")
            body = captured[0]
            self.assertEqual(body["model"], "typesafe/jev-1.13")
            self.assertEqual(body["state"]["chat"]["style"], "轻松自然")
            self.assertIn("reply_worthwhile", body["questions"])
            self.assertEqual(body["provider"]["max_price"], {"prompt": 0.05, "completion": 0, "request": 0})
            self.assertEqual(ledger.total_usd(), Decimal("0.00001"))

    def test_jev_reservation_counts_state_for_every_independent_question(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            captured = []
            def fake_paid(body, key):
                captured.append(body)
                return {"answers": {}}
            with patch.object(evaluation, "_paid_jev_request", side_effect=fake_paid):
                with self.assertRaises(ValueError):
                    evaluation.run_jev(ledger, CASES["friend_multiturn_frustration"],
                                       ("听着就累",), "dummy-key")
            body = captured[0]
            single_payload = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            single_reserve = Decimal(single_payload + 4096) * Decimal("0.05") / Decimal(1_000_000)
            self.assertGreater(ledger.total_usd(), single_reserve)

    def test_jev_failure_retains_draft_candidates(self):
        with TemporaryDirectory() as directory:
            ledger = evaluation.BudgetLedger(Path(directory) / "ledger.json")
            response = {"choices": [{"message": {"content": '{"candidates":["太好了！"]}'}}],
                        "usage": {"cost": "0.00001"}}
            price = {"prompt": "0.0000001", "completion": "0.0000001"}
            with patch.object(evaluation, "_paid_request", return_value=response), \
                 patch.object(evaluation, "run_jev", side_effect=ValueError("private data")):
                output = evaluation._run_one(ledger, "joy", CASES["joy"], "candidate",
                                             "fictional/model", price, "dummy-key", True)
            self.assertEqual(output["candidates"], ["太好了！"])
            self.assertEqual(output["jev"], {"error": "invalid_response"})
            self.assertTrue(output["halted"])

    def test_http_failure_stops_batch_persists_error_and_returns_nonzero(self):
        with TemporaryDirectory() as directory:
            build = Path(directory)
            calls = []
            def fail_first(body, key):
                calls.append(body["model"])
                error = urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
                error.close()
                raise error
            args = argparse.Namespace(case=["joy", "venting"], baseline=False, with_jev=False,
                                      candidate_model="fictional/model")
            windows_os = SimpleNamespace(name="nt", open=os.open, close=os.close, O_CREAT=os.O_CREAT,
                O_EXCL=os.O_EXCL, O_WRONLY=os.O_WRONLY, fsync=os.fsync, replace=os.replace)
            with patch.object(evaluation, "BUILD", build), \
                 patch.object(evaluation, "os", windows_os), \
                 patch.object(evaluation, "public_pricing", return_value={
                     "prompt": "0.0000001", "completion": "0.0000001"}), \
                 patch("app.credentials.load_saved_key", return_value="synthetic-key"), \
                 patch.object(evaluation, "_paid_request", side_effect=fail_first):
                status = evaluation._run(args)
            output = json.loads(next(build.glob("synthetic-*.json")).read_text(encoding="utf-8"))
            ledger = json.loads((build / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(status, 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(output["results"][0]["error"], "http:401")
            self.assertEqual(len(output["results"]), 1)
            self.assertEqual(ledger["reservations"][0]["status"], "unknown")

    def test_reservation_uses_full_request_bytes_and_rejects_unpriced_fee(self):
        body = {"model": "fictional/model", "messages": [{"role": "user", "content": "中文"}],
                "max_tokens": 1600, "provider": {"max_price": {"prompt": 1, "completion": 1, "request": 0}}}
        price = {"prompt": "0.000001", "completion": "0.000002", "request": "0"}
        amount = evaluation.reservation_usd(body, price)
        self.assertGreaterEqual(amount, Decimal(len(json.dumps(body, ensure_ascii=False).encode("utf-8"))) * Decimal("0.000001"))
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.reservation_usd(body, {**price, "request": "0.01"})

    def test_override_prices_raise_both_reservation_and_provider_caps(self):
        price = {"prompt": "0.00000032", "completion": "0.00000128",
                 "input_cache_read": "0.000000064",
                 "overrides": [{"min_prompt_tokens": 256000, "prompt": "0.00000096",
                                "completion": "0.00000384"}]}
        body = {"model": "fictional/model", "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1600, "provider": {"require_parameters": True}}
        guarded = evaluation._with_price_cap(body, price)
        self.assertEqual(guarded["provider"]["max_price"],
                         {"prompt": 0.96, "completion": 3.84, "request": 0})
        self.assertGreater(evaluation.reservation_usd(guarded, price), Decimal("0.006"))
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.reservation_usd(body, {**price, "mystery_fee": "0.000001"})


if __name__ == "__main__":
    unittest.main()
