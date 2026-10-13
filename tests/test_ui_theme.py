from dataclasses import FrozenInstanceError
import unittest

from app.ui_theme import PALETTE


class UiThemeTests(unittest.TestCase):
    def test_confirmed_semantic_palette_is_exact_and_frozen(self):
        self.assertEqual(PALETTE.window, "#F2F5F3")
        self.assertEqual(PALETTE.surface, "#FFFFFF")
        self.assertEqual(PALETTE.text, "#182923")
        self.assertEqual(PALETTE.muted, "#64766E")
        self.assertEqual(PALETTE.divider, "#DCE5DF")
        self.assertEqual(PALETTE.primary, "#176B50")
        self.assertEqual(PALETTE.recommended, "#EDF6F0")
        self.assertEqual(PALETTE.candidate, "#F8FAF9")
        self.assertEqual(PALETTE.auxiliary, "#EEF2F7")
        self.assertEqual(PALETTE.warning, "#93611D")
        self.assertEqual(PALETTE.error, "#B44832")
        with self.assertRaises(FrozenInstanceError):
            PALETTE.primary = "#000000"


if __name__ == "__main__":
    unittest.main()
