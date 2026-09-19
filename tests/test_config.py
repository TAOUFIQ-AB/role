import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from config import _env_int


class ConfigTests(unittest.TestCase):
    def test_invalid_integer_falls_back_instead_of_crashing(self):
        with patch.dict(os.environ, {"TEST_INTEGER_SETTING": "not-a-number"}, clear=False):
            self.assertEqual(_env_int("TEST_INTEGER_SETTING", 42, 0), 42)

    def test_integer_minimum_is_enforced(self):
        with patch.dict(os.environ, {"TEST_INTEGER_SETTING": "-5"}, clear=False):
            self.assertEqual(_env_int("TEST_INTEGER_SETTING", 42, 0), 0)


if __name__ == "__main__":
    unittest.main()
