"""Small, dependency-free fuzzy matching for OCR text."""

import difflib


def similar(a, b):
    """Match identical text and tolerate the existing small OCR variations."""
    if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.75:
        return True
    return len(a) == len(b) >= 3 and sum(x != y for x, y in zip(a, b)) <= 1
