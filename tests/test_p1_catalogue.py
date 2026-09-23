import json, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
EVENTS = {"SessionStart","UserPromptSubmit","PreToolUse","PostToolUse","Stop","SessionEnd"}
IDS = ["automatic-handoff-restore","automatic-handoff","wiki-loader","wiki-trigger","long-prompt-brief","main-branch-guard","wiki-read-log",
       "no-speculation","ask-after-wiki","verify-before-done","session-capture",
       "wiki-auto-update"]

class CatalogueTest(unittest.TestCase):
    def setUp(self):
        self.cat = json.loads((ROOT/"kit/catalogue.json").read_text(encoding="utf-8"))
    def test_ids_exact(self):
        self.assertEqual([h["id"] for h in self.cat], IDS)
    def test_fields(self):
        for h in self.cat:
            self.assertIn(h["event"], EVENTS)
            for k in ("matcher","script","title_ko","desc_ko","example_ko"):
                self.assertIsInstance(h[k], str)
            self.assertTrue(h["script"].endswith(".py"))
            self.assertIsInstance(h["timeout"], int)
            self.assertIsInstance(h["default"], bool)
            self.assertTrue(set(h["targets"]) <= {"claude","codex"} and h["targets"])
            self.assertTrue(h["desc_ko"] and h["title_ko"])

    def test_english_fields_present_next_to_korean(self):
        """Task 6.15 step 1: every hook has title_en/desc_en/example_en so
        the page can show the chosen language's fields."""
        for h in self.cat:
            for k in ("title_en", "desc_en", "example_en"):
                self.assertIn(k, h, f"{h['id']} missing {k}")
                self.assertIsInstance(h[k], str)
                self.assertTrue(h[k].strip(), f"{h['id']}.{k} is empty")
    def test_session_end_timeout_fits_codex(self):
        se = [h for h in self.cat if h["event"] == "SessionEnd"]
        self.assertTrue(all(h["timeout"] <= 3 for h in se))
