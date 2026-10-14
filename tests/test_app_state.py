from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app_state import normalize_database_path


class DatabasePathStateTests(unittest.TestCase):
    def test_trims_whitespace(self) -> None:
        self.assertEqual(
            normalize_database_path("  C:\\Keystone\\data.sqlite  "),
            "C:\\Keystone\\data.sqlite",
        )

    def test_removes_pasted_quotes(self) -> None:
        self.assertEqual(
            normalize_database_path('"C:\\Keystone\\market data.sqlite"'),
            "C:\\Keystone\\market data.sqlite",
        )

    def test_empty_value_is_safe(self) -> None:
        self.assertEqual(normalize_database_path(None), "")


if __name__ == "__main__":
    unittest.main()
