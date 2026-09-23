from __future__ import annotations
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from kit.skills_install import install_skills
import kit.obsidian as obsidian
from kit.obsidian import find_obsidian, install_hint, write_obsidian_config, open_url

REPO_ROOT = Path(__file__).resolve().parents[1]


class InstallSkillsTest(unittest.TestCase):
    def test_writes_skills_for_both_targets_with_path_substituted(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            install_skills(home, wiki_path, ["claude", "codex"])

            for base in (".claude", ".codex"):
                for name in ("kb", "kb-ingest", "kb-lint"):
                    p = home / base / "skills" / name / "SKILL.md"
                    self.assertTrue(p.exists(), p)
                    text = p.read_text(encoding="utf-8")
                    self.assertIn(str(wiki_path), text)
                    self.assertNotIn("{{WIKI_PATH}}", text)
                    self.assertIn("<!-- keel -->", text.splitlines()[:10])

    def test_does_not_overwrite_user_authored_skill(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            user_skill = home / ".claude" / "skills" / "kb" / "SKILL.md"
            user_skill.parent.mkdir(parents=True)
            user_skill.write_text("# my own wiki skill\n", encoding="utf-8")

            install_skills(home, wiki_path, ["claude"])

            self.assertEqual(user_skill.read_text(encoding="utf-8"), "# my own wiki skill\n")

    def test_overwrites_kit_marked_skill(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            install_skills(home, wiki_path, ["claude"])
            install_skills(home, wiki_path / "renamed", ["claude"])
            text = (home / ".claude" / "skills" / "kb" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(str(wiki_path / "renamed"), text)

    def test_overwrites_when_marker_is_within_first_ten_lines_not_at_start(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            existing_path = home / ".claude" / "skills" / "kb" / "SKILL.md"
            existing_path.parent.mkdir(parents=True)
            content = (
                "---\n"
                "name: wiki\n"
                "description: old\n"
                "---\n"
                "<!-- keel -->\n"
                "\n"
                "old body\n"
            )
            existing_path.write_text(content, encoding="utf-8")

            install_skills(home, wiki_path, ["claude"])

            text = existing_path.read_text(encoding="utf-8")
            self.assertNotEqual(text, content)
            self.assertIn(str(wiki_path), text)


class InstallSkillsWrittenListTest(unittest.TestCase):
    def test_written_list_contains_only_the_listed_dest_paths(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            written = install_skills(home, wiki_path, ["claude", "codex"])

            expected = {
                home / base / "skills" / name / "SKILL.md"
                for base in (".claude", ".codex")
                for name in ("kb", "kb-ingest", "kb-lint")
            }
            self.assertEqual(set(written), expected)


class RemoveSkillsTest(unittest.TestCase):
    def test_removes_kit_marked_files_and_empty_folders(self):
        from kit.skills_install import remove_skills

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            install_skills(home, wiki_path, ["claude", "codex"])

            removed = remove_skills(home, ["claude", "codex"])
            self.assertEqual(len(removed), 6)
            for base in (".claude", ".codex"):
                for name in ("kb", "kb-ingest", "kb-lint"):
                    skill_dir = home / base / "skills" / name
                    self.assertFalse(skill_dir.exists(), skill_dir)

    def test_leaves_user_authored_skill_untouched(self):
        from kit.skills_install import remove_skills

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            user_skill = home / ".claude" / "skills" / "kb" / "SKILL.md"
            user_skill.parent.mkdir(parents=True)
            user_skill.write_text("# my own wiki skill\n", encoding="utf-8")

            removed = remove_skills(home, ["claude"])

            self.assertEqual(removed, [])
            self.assertTrue(user_skill.exists())
            self.assertEqual(user_skill.read_text(encoding="utf-8"), "# my own wiki skill\n")


class SkillSourceFrontMatterTest(unittest.TestCase):
    def test_kit_skill_sources_have_frontmatter_then_marker(self):
        for name in ("kb", "kb-ingest", "kb-lint"):
            path = REPO_ROOT / "kit" / "skills" / name / "SKILL.md"
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "---")
            closing_idx = lines.index("---", 1)
            yaml_block = "\n".join(lines[1:closing_idx])
            self.assertIn("name:", yaml_block)
            self.assertIn("description:", yaml_block)
            self.assertEqual(lines[closing_idx + 1], "<!-- keel -->")


class WikiLintSkillTest(unittest.TestCase):
    def test_front_matter_name(self):
        path = REPO_ROOT / "kit" / "skills" / "kb-lint" / "SKILL.md"
        lines = path.read_text(encoding="utf-8").splitlines()
        closing_idx = lines.index("---", 1)
        yaml_block = "\n".join(lines[1:closing_idx])
        self.assertIn("name: kb-lint", yaml_block)


class ObsidianTest(unittest.TestCase):
    def test_find_obsidian_checks_applications_under_home(self):
        # Patched to an empty global-candidate list so this test never depends on
        # whether Obsidian happens to be installed at /Applications on this machine.
        with mock.patch.object(obsidian, "GLOBAL_CANDIDATES", []):
            with tempfile.TemporaryDirectory() as td:
                home = Path(td)
                self.assertIsNone(find_obsidian(home))
                app_dir = home / "Applications" / "Obsidian.app"
                app_dir.mkdir(parents=True)
                self.assertEqual(find_obsidian(home), str(app_dir))

    def test_install_hint(self):
        hint = install_hint()
        self.assertEqual(hint["brew"], "brew install --cask obsidian")
        self.assertEqual(hint["url"], "https://obsidian.md/download")

    def test_write_obsidian_config_creates_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            wiki_path = Path(td) / "llm-wiki"
            wiki_path.mkdir()
            created = write_obsidian_config(wiki_path)
            self.assertTrue(created)
            for name in ("app.json", "core-plugins.json", "graph.json"):
                self.assertTrue((wiki_path / ".obsidian" / name).exists())

            marker = wiki_path / ".obsidian" / "app.json"
            marker.write_text('{"edited": true}', encoding="utf-8")
            second = write_obsidian_config(wiki_path)
            self.assertEqual(marker.read_text(encoding="utf-8"), '{"edited": true}')

    def test_open_url_quotes_spaces(self):
        wiki_path = Path("/tmp/my wiki")
        url = open_url(wiki_path)
        self.assertTrue(url.startswith("obsidian://open?path="))
        self.assertIn(urllib.parse.quote(str(wiki_path)), url)
        self.assertIn("%20", url)


class InstallSkillsGrokTest(unittest.TestCase):
    """Task 6.18 step 3: Grok reads ~/.claude/skills/ by default, so a
    ~/.grok/skills/ copy is written only when Claude was not selected."""

    def test_grok_only_writes_to_grok_skills(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            install_skills(home, wiki_path, ["grok"])
            for name in ("kb", "kb-ingest", "kb-lint"):
                p = home / ".grok" / "skills" / name / "SKILL.md"
                self.assertTrue(p.exists(), p)

    def test_claude_and_grok_skips_grok_skills_dir(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            install_skills(home, wiki_path, ["claude", "grok"])
            for name in ("kb", "kb-ingest", "kb-lint"):
                self.assertTrue((home / ".claude" / "skills" / name / "SKILL.md").exists())
                self.assertFalse((home / ".grok" / "skills" / name / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
