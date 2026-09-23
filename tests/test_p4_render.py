from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from kit.history import Change
from kit.render_repo import render_repo
from kit.wiki_init import init_wiki


def _change(sha, is_merge, author, date, subject, title, pr, areas):
    return Change(
        sha=sha,
        is_merge=is_merge,
        author=author,
        date=date,
        subject=subject,
        title=title,
        pr=pr,
        areas=areas,
    )


class RenderRepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = Path(self.tmp) / "home"
        self.wiki = self.home / "wiki"
        init_wiki(self.home, self.wiki, "ko", git_init=False)

        self.changes = [
            _change("s2", False, "Bob", "2024-06-13T09:00:00+00:00", "Fix bug (#9)", "Fix bug", 9, ["src"]),
            _change(
                "s1",
                True,
                "Alice",
                "2024-05-01T10:00:00+00:00",
                "Merge pull request #7 from me/feat",
                "Add feature A",
                7,
                ["docs", "src"],
            ),
        ]

    def test_render_empty_window(self):
        written = render_repo(self.wiki, "acme/widget", "main", 30, [], "ko")
        index_path = self.wiki / "repos" / "widget" / "index.md"
        self.assertIn(index_path, written)
        self.assertFalse((self.wiki / "repos" / "widget" / "timeline").exists())
        self.assertFalse((self.wiki / "repos" / "widget" / "areas").exists())

    def test_area_and_month_pages_exist_and_link_back(self):
        written = render_repo(self.wiki, "acme/widget", "main", 30, self.changes, "ko")
        month_page = self.wiki / "repos" / "widget" / "timeline" / "2024-06.md"
        area_page = self.wiki / "repos" / "widget" / "areas" / "src.md"
        self.assertIn(month_page, written)
        self.assertIn(area_page, written)
        self.assertIn("[[repos/widget/index|", month_page.read_text(encoding="utf-8"))
        self.assertIn("[[repos/widget/index|", area_page.read_text(encoding="utf-8"))
        # both months present
        self.assertTrue((self.wiki / "repos" / "widget" / "timeline" / "2024-05.md").exists())
        # docs area also present
        self.assertTrue((self.wiki / "repos" / "widget" / "areas" / "docs.md").exists())

    def test_summaries_inserted(self):
        summaries = {
            "index": "요약 문단입니다.",
            "months": {"2024-06": "6월 요약"},
            "areas": {"src": "src 영역 요약"},
        }
        render_repo(self.wiki, "acme/widget", "main", 30, self.changes, "ko", summaries=summaries)
        index_text = (self.wiki / "repos" / "widget" / "index.md").read_text(encoding="utf-8")
        month_text = (self.wiki / "repos" / "widget" / "timeline" / "2024-06.md").read_text(encoding="utf-8")
        area_text = (self.wiki / "repos" / "widget" / "areas" / "src.md").read_text(encoding="utf-8")
        self.assertIn("요약 문단입니다.", index_text)
        self.assertIn("6월 요약", month_text)
        self.assertIn("src 영역 요약", area_text)

    def test_rerun_overwrites_without_duplicating_index_link(self):
        render_repo(self.wiki, "acme/widget", "main", 30, self.changes, "ko")
        render_repo(self.wiki, "acme/widget", "main", 30, self.changes, "ko")
        wiki_index = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertEqual(wiki_index.count("repos/widget/index|acme/widget"), 1)

    def test_wiki_index_gets_repo_link(self):
        render_repo(self.wiki, "acme/widget", "main", 30, self.changes, "ko")
        wiki_index = (self.wiki / "index.md").read_text(encoding="utf-8")
        self.assertIn("[[repos/widget/index|acme/widget]]", wiki_index)

    def test_rerun_with_fewer_months_and_areas_removes_stale_pages(self):
        wide = [
            _change("w1", False, "Bob", "2024-01-10T09:00:00+00:00", "Jan change", "Jan change", None, ["jan"]),
            _change("w2", False, "Bob", "2024-02-10T09:00:00+00:00", "Feb change", "Feb change", None, ["feb"]),
        ]
        render_repo(self.wiki, "acme/widget", "main", 180, wide, "ko")
        self.assertTrue((self.wiki / "repos" / "widget" / "timeline" / "2024-01.md").exists())
        self.assertTrue((self.wiki / "repos" / "widget" / "areas" / "jan.md").exists())

        narrow = [
            _change("n1", False, "Bob", "2024-06-10T09:00:00+00:00", "June change", "June change", None, ["june"]),
        ]
        render_repo(self.wiki, "acme/widget", "main", 30, narrow, "ko")

        self.assertFalse((self.wiki / "repos" / "widget" / "timeline" / "2024-01.md").exists())
        self.assertFalse((self.wiki / "repos" / "widget" / "timeline" / "2024-02.md").exists())
        self.assertFalse((self.wiki / "repos" / "widget" / "areas" / "jan.md").exists())
        self.assertFalse((self.wiki / "repos" / "widget" / "areas" / "feb.md").exists())
        self.assertTrue((self.wiki / "repos" / "widget" / "timeline" / "2024-06.md").exists())
        self.assertTrue((self.wiki / "repos" / "widget" / "areas" / "june.md").exists())

    def test_area_slug_collisions_and_unicode(self):
        changes = [
            _change("a1", False, "Bob", "2024-06-01T09:00:00+00:00", "c1", "c1", None, ["docs"]),
            _change("a2", False, "Bob", "2024-06-02T09:00:00+00:00", "c2", "c2", None, ["Docs"]),
            _change("a3", False, "Bob", "2024-06-03T09:00:00+00:00", "c3", "c3", None, [".github"]),
            _change("a4", False, "Bob", "2024-06-04T09:00:00+00:00", "c4", "c4", None, ["github"]),
            _change("a5", False, "Bob", "2024-06-05T09:00:00+00:00", "c5", "c5", None, ["문서"]),
        ]
        render_repo(self.wiki, "acme/widget", "main", 30, changes, "ko")
        areas_dir = self.wiki / "repos" / "widget" / "areas"
        files = {p.name for p in areas_dir.glob("*.md")}
        self.assertEqual(len(files), 5)
        self.assertIn("문서.md", files)
        self.assertTrue(any(n.startswith("dot-github") for n in files))
        self.assertIn("github.md", files)

    def test_change_title_brackets_and_pipes_escaped(self):
        changes = [
            _change("t1", False, "Bob", "2024-06-01T09:00:00+00:00", "add [[x]] thing", "add [[x]] thing", None, ["src"]),
            _change("t2", False, "Bob", "2024-06-02T09:00:00+00:00", "a | b", "a | b", None, ["src"]),
        ]
        render_repo(self.wiki, "acme/widget", "main", 30, changes, "ko")
        month_text = (self.wiki / "repos" / "widget" / "timeline" / "2024-06.md").read_text(encoding="utf-8")
        self.assertIn("\\[\\[x\\]\\]", month_text)
        self.assertIn("a \\| b", month_text)

    def test_local_repo_uses_local_prefix_fallback_on_collision(self):
        render_repo(self.wiki, "acme/widget", "main", 30, self.changes, "ko")
        local_changes = [
            _change("l1", False, "Carl", "2024-06-01T09:00:00+00:00", "local change", "local change", None, ["src"]),
        ]
        render_repo(self.wiki, "widget", "main", 30, local_changes, "ko")

        self.assertTrue((self.wiki / "repos" / "local__widget" / "index.md").exists())
        github_index = (self.wiki / "repos" / "widget" / "index.md").read_text(encoding="utf-8")
        self.assertIn("<!-- repo: acme/widget -->", github_index)


if __name__ == "__main__":
    unittest.main()
