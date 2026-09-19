import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from browser import BrowserManager


class BrowserCookieTests(unittest.TestCase):
    def test_semicolon_cookie_parse(self):
        cookies = BrowserManager._parse_cookies("sessionid=abc; csrftoken=xyz")
        self.assertEqual({c["name"] for c in cookies}, {"sessionid", "csrftoken"})

    def test_json_cookie_parse_preserves_host_domain(self):
        raw = json.dumps([{
            "name": "sessionid",
            "value": "abc",
            "domain": "www.instagram.com",
            "path": "/",
            "secure": True,
        }])
        cookie = BrowserManager._parse_cookies(raw)[0]
        self.assertEqual(cookie["domain"], "www.instagram.com")


if __name__ == "__main__":
    unittest.main()
