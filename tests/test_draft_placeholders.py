import json
import unittest
from unittest.mock import patch

from core.draft import draft_candidates
from core.service_error import ServiceError


class DraftPlaceholderTests(unittest.TestCase):
    def generate(self, candidates):
        with patch("core.draft._chat", return_value=json.dumps({"candidates": candidates})):
            return draft_candidates([("her", "哪天讨论？")], "同事", "synthetic-key")

    def test_unfilled_date_and_name_templates_cannot_be_offered_alongside_usable_reply(self):
        for template in ("是指X月X日那个周三吗？", "x号方便吗？", "你好，[待填姓名]", "时间是{date}"):
            with self.subTest(template=template):
                self.assertEqual(self.generate([template, "你说的是几号？我对一下日程"]),
                                 ["你说的是几号？我对一下日程"])

    def test_only_templates_report_invalid_response_instead_of_empty_usable_result(self):
        with self.assertRaises(ServiceError):
            self.generate(["X月X日可以吗？"])

    def test_concrete_questions_future_proposals_and_exact_ack_remain_usable(self):
        candidates = ["收到", "我们先对一下日程？", "我看看日程再跟你商量", "你用过X平台吗？"]
        self.assertEqual(self.generate(candidates), candidates)
