from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from pivot_core import classify_strong_patterns, load_config


SETTINGS = load_config(PROJECT_DIR / "config.json")["pattern_settings"]


def bar(opening: float, high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"open": opening, "high": high, "low": low, "close": close})


class StrongPatternTests(unittest.TestCase):
    def test_bullish_piercing_is_detected(self) -> None:
        previous = bar(100.0, 101.0, 94.0, 95.0)
        current = bar(94.9, 99.0, 94.0, 98.5)
        flags = classify_strong_patterns(previous, current, 5.0, SETTINGS)
        self.assertTrue(flags["bullish_piercing"])
        self.assertFalse(flags["bearish_dark_cloud"])

    def test_long_handle_hammer_is_detected(self) -> None:
        previous = bar(100.0, 102.0, 96.0, 98.0)
        current = bar(97.0, 99.5, 90.0, 99.0)
        flags = classify_strong_patterns(previous, current, 2.0, SETTINGS)
        self.assertTrue(flags["long_handle_hammer"])
        self.assertFalse(flags["long_handle_shooting_star"])

    def test_bullish_full_outside_reversal_is_detected(self) -> None:
        previous = bar(100.0, 102.0, 96.0, 98.0)
        current = bar(97.0, 103.0, 95.0, 102.5)
        flags = classify_strong_patterns(previous, current, 2.0, SETTINGS)
        self.assertTrue(flags["bullish_full_outside"])
        self.assertFalse(flags["bearish_full_outside"])

    def test_ordinary_candle_does_not_create_approved_pattern(self) -> None:
        previous = bar(100.0, 102.0, 98.0, 101.0)
        current = bar(101.0, 102.0, 99.5, 101.5)
        flags = classify_strong_patterns(previous, current, 1.5, SETTINGS)
        self.assertFalse(any(flags.values()))


if __name__ == "__main__":
    unittest.main()
