from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

from kit.wiki_init import write_config, init_wiki, add_index_links
from kit import install_hooks

ROOT = Path(__file__).resolve().parents[1]


class WriteConfigTest(unittest.TestCase):
    def test_writes_config(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            path = write_config(home, wiki_path, "ko")
            self.assertEqual(path, home / ".keel" / "config.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data, {"wiki_path": str(wiki_path), "language": "ko"})


class InitWikiTest(unittest.TestCase):
    def test_creating_kb_preserves_installed_handoff_settings(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            install_hooks.install(home, ROOT, ["automatic-handoff"], ["codex"],
                                  handoff_threshold=70, handoff_targets=["codex"])
            init_wiki(home, home / "knowledge-base", "ko", git_init=False)
            config = json.loads((home / ".keel" / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["handoff_threshold"], 70)
            self.assertEqual(config["handoff_targets"], ["codex"])

    def test_creates_all_paths_and_config(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            created = init_wiki(home, wiki_path, "ko")
            for rel in (
                "index.md",
                "schema.md",
                "raw/sessions/.gitkeep",
                "wiki/.gitkeep",
                "repos/.gitkeep",
                "local/.gitkeep",
                ".gitignore",
            ):
                self.assertTrue((wiki_path / rel).exists(), rel)
            self.assertTrue((wiki_path / ".git").exists())
            config_path = home / ".keel" / "config.json"
            self.assertTrue(config_path.exists())
            self.assertEqual(
                (wiki_path / ".gitignore").read_text(encoding="utf-8"),
                ".obsidian/workspace*.json\n.DS_Store\n",
            )
            self.assertTrue(len(created) > 0)

    def test_second_call_creates_nothing_and_preserves_edits(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")
            (wiki_path / "index.md").write_text("edited content", encoding="utf-8")
            created = init_wiki(home, wiki_path, "ko")
            self.assertEqual(created, [])
            self.assertEqual((wiki_path / "index.md").read_text(encoding="utf-8"), "edited content")

    def test_path_outside_home_raises(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            outside = Path(td) / "elsewhere"
            with self.assertRaises(ValueError):
                init_wiki(home, outside, "ko")

    def test_no_git_init_when_flag_false(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko", git_init=False)
            self.assertFalse((wiki_path / ".git").exists())


class AddIndexLinksTest(unittest.TestCase):
    def test_adds_section_and_links_without_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")
            add_index_links(wiki_path, "주제", ["topic-a", "topic-b"])
            add_index_links(wiki_path, "주제", ["topic-a"])
            text = (wiki_path / "index.md").read_text(encoding="utf-8")
            self.assertEqual(text.count("[[topic-a]]"), 1)
            self.assertEqual(text.count("[[topic-b]]"), 1)
            self.assertIn("## 주제", text)

    def test_h3_topic_heading_does_not_block_new_h2_section(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            wiki_path.mkdir(parents=True)
            index_path = wiki_path / "index.md"
            index_path.write_text(
                "# 위키\n\n### 주제\n\nsome sub-note\n",
                encoding="utf-8",
            )

            add_index_links(wiki_path, "주제", ["topic-a"])

            text = index_path.read_text(encoding="utf-8")
            lines = [line.strip() for line in text.split("\n")]
            self.assertIn("### 주제", lines)
            self.assertIn("## 주제", lines)
            self.assertIn("[[topic-a]]", text)


if __name__ == "__main__":
    unittest.main()
