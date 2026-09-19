import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from ai_router import _ProviderHealth, parse_hashtags


class AIRouterTests(unittest.TestCase):
    def test_hashtags_are_normalized_and_deduplicated(self):
        self.assertEqual(
            parse_hashtags("#Viral text #viral #Car_Edit #fyp", limit=3),
            ["#viral", "#car_edit", "#fyp"],
        )

    def test_provider_health_reset(self):
        health = _ProviderHealth()
        health.mark_quota_failed("gemini")
        self.assertFalse(health.is_available("gemini"))
        health.reset("gemini")
        self.assertTrue(health.is_available("gemini"))


if __name__ == "__main__":
    unittest.main()
