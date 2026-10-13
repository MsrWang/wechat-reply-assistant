import unittest

from app.text_similarity import similar


class TextSimilarityTests(unittest.TestCase):
    def test_identical_titles_match(self):
        self.assertTrue(similar("当前聊天", "当前聊天"))

    def test_ocr_jitter_at_similarity_threshold_matches(self):
        self.assertTrue(similar("当前聊天", "当前聊大"))

    def test_one_character_change_in_short_title_matches(self):
        self.assertTrue(similar("甲乙丙", "甲乙丁"))

    def test_multiple_character_changes_do_not_match(self):
        self.assertFalse(similar("甲乙丙丁", "甲乙戊己"))


if __name__ == "__main__":
    unittest.main()
