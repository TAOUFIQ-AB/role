import pathlib
import unittest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_workflows_parse_as_yaml(self):
        for path in (ROOT / ".github" / "workflows").glob("*.yml"):
            with self.subTest(path=path.name):
                data = yaml.safe_load(path.read_text())
                self.assertIsInstance(data, dict)
                self.assertIn("jobs", data)

    def test_main_workflow_scripts_exist(self):
        workflow = (ROOT / ".github" / "workflows" / "reels_agent.yml").read_text()
        for rel in (
            ".github/scripts/write_tt_cookies.py",
            ".github/scripts/validate_instagram_cookies.py",
        ):
            self.assertIn(rel, workflow)
            self.assertTrue((ROOT / rel).is_file())

    def test_legacy_gemini_sdk_removed(self):
        for path in (ROOT / "app").glob("*.py"):
            self.assertNotIn("google.generativeai", path.read_text(), path.name)

    def test_remote_desktop_requires_authentication(self):
        workflow = (ROOT / ".github" / "workflows" / "reels_agent.yml").read_text()
        entrypoint = (ROOT / "entrypoint.sh").read_text()
        self.assertIn("auth=env", workflow)
        self.assertIn("auth=env", entrypoint)
        self.assertIn("XPRA_PASSWORD secret is required", workflow)
        self.assertNotIn("Password: {os.environ.get('XPRA_PASSWORD'", workflow)

    def test_current_provider_defaults_are_present(self):
        config = (ROOT / "app" / "config.py").read_text()
        self.assertIn("qwen/qwen3.6-27b", config)
        self.assertIn("openai/gpt-oss-120b", config)
        self.assertIn("openrouter/free", config)
        self.assertIn('"groq,openrouter"', config)
        self.assertNotIn("gemini-3.8-flash", config)
        self.assertNotIn("llama3-70b-8192", config)

    def test_docker_matches_pinned_playwright_version(self):
        requirements = (ROOT / "requirements.txt").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("playwright==1.63.0", requirements)
        self.assertIn("playwright/python:v1.63.0-noble", dockerfile)


if __name__ == "__main__":
    unittest.main()
