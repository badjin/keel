from __future__ import annotations
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kit.local_index import (
    safe_under,
    list_dirs,
    build_local_index,
    git_branches,
    _build_tree_lines,
    _find_git_subfolders,
    _marker,
)
from kit.wiki_init import init_wiki


class SafeUnderTest(unittest.TestCase):
    def test_refuses_outside_home(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            (home / "sub").mkdir()

            with self.assertRaises(ValueError):
                safe_under(home, Path("/etc"))

            with self.assertRaises(ValueError):
                safe_under(home, home / ".." / "x")

            outside = Path(td) / "outside"
            outside.mkdir()
            link = home / "escape"
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unsupported")
            with self.assertRaises(ValueError):
                safe_under(home, link)

    def test_allows_home_and_subdir(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            (home / "sub").mkdir()
            self.assertEqual(safe_under(home, home), home.resolve())
            self.assertEqual(safe_under(home, home / "sub"), (home / "sub").resolve())


class ListDirsTest(unittest.TestCase):
    def test_lists_non_hidden_subdirs_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            (home / "b").mkdir()
            (home / "a").mkdir()
            (home / ".hidden").mkdir()
            (home / "a" / ".git").mkdir()
            (home / "file.txt").write_text("x", encoding="utf-8")

            dirs = list_dirs(home, home)
            names = [d["name"] for d in dirs]
            self.assertEqual(names, ["a", "b"])
            a = next(d for d in dirs if d["name"] == "a")
            self.assertTrue(a["is_git"])
            b = next(d for d in dirs if d["name"] == "b")
            self.assertFalse(b["is_git"])

    def test_unreadable_subdir_is_skipped_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            (home / "ok").mkdir()
            bad = home / "bad"
            bad.mkdir()
            bad.chmod(0o000)
            try:
                dirs = list_dirs(home, home)
            finally:
                bad.chmod(0o755)
            names = [d["name"] for d in dirs]
            self.assertIn("ok", names)

    def test_unreadable_directory_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            home.chmod(0o000)
            try:
                dirs = list_dirs(home, home)
            finally:
                home.chmod(0o755)
            self.assertEqual(dirs, [])

    def test_symlinked_subdir_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            real = Path(td) / "real"
            real.mkdir()
            (home / "kept").mkdir()
            link = home / "linked"
            try:
                os.symlink(real, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unsupported")

            dirs = list_dirs(home, home)
            names = [d["name"] for d in dirs]
            self.assertEqual(names, ["kept"])

    def test_child_git_probe_oserror_yields_is_git_false_without_raising(self):
        # On Python 3.9, Path.exists() re-raises PermissionError instead of
        # swallowing it. Simulate that for the per-child `.git` probe.
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            (home / "sub").mkdir()

            real_exists = Path.exists

            def flaky_exists(self):
                if self.name == ".git":
                    raise PermissionError("no access")
                return real_exists(self)

            with mock.patch.object(Path, "exists", flaky_exists):
                dirs = list_dirs(home, home)
            names = {d["name"]: d for d in dirs}
            self.assertIn("sub", names)
            self.assertFalse(names["sub"]["is_git"])


class BuildLocalIndexTest(unittest.TestCase):
    def test_builds_index_with_tree_readme_and_git_subfolders(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            folder = home / "myproject"
            (folder / "node_modules" / "pkg").mkdir(parents=True)
            (folder / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
            (folder / "src").mkdir()
            (folder / "src" / "main.py").write_text("x", encoding="utf-8")
            (folder / "sub-repo" / ".git").mkdir(parents=True)
            (folder / "README.md").write_text(
                "\n".join(f"line {i}" for i in range(30)), encoding="utf-8"
            )

            created = build_local_index(home, wiki_path, [folder], "ko")
            self.assertTrue(created)

            index_page = wiki_path / "local" / "myproject" / "index.md"
            self.assertTrue(index_page.exists())
            text = index_page.read_text(encoding="utf-8")
            self.assertIn(str(folder), text)
            self.assertNotIn("node_modules", text)
            self.assertIn("line 0", text)
            self.assertNotIn("line 25", text)
            self.assertIn("sub-repo", text)

            wiki_index = (wiki_path / "index.md").read_text(encoding="utf-8")
            self.assertEqual(wiki_index.count("local/myproject/index"), 1)

    def test_tree_reports_exact_remainder_count_past_cap(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            folder = home / "bigfolder"
            folder.mkdir()
            for i in range(250):
                (folder / f"file{i:03d}.txt").write_text("x", encoding="utf-8")

            created = build_local_index(home, wiki_path, [folder], "ko")
            text = created[0].read_text(encoding="utf-8")
            self.assertIn("… (50 more)", text)

    def test_folder_that_is_itself_a_git_repo_is_listed(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            folder = home / "myrepo"
            (folder / ".git").mkdir(parents=True)
            (folder / "src").mkdir()
            (folder / "src" / "main.py").write_text("x", encoding="utf-8")

            created = build_local_index(home, wiki_path, [folder], "ko")
            text = created[0].read_text(encoding="utf-8")
            self.assertIn(f"- {folder.name}", text)
            self.assertNotIn("- .\n", text)

    def test_korean_folder_names_get_distinct_non_overwriting_slugs(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            docs = home / "문서"
            docs.mkdir()
            (docs / "a.txt").write_text("x", encoding="utf-8")
            photos = home / "사진"
            photos.mkdir()
            (photos / "b.txt").write_text("x", encoding="utf-8")

            created_docs = build_local_index(home, wiki_path, [docs], "ko")
            created_photos = build_local_index(home, wiki_path, [photos], "ko")

            self.assertTrue(created_docs)
            self.assertTrue(created_photos)
            docs_slug = created_docs[0].parent.name
            photos_slug = created_photos[0].parent.name
            self.assertNotEqual(docs_slug, photos_slug)
            self.assertTrue(docs_slug.strip())
            self.assertTrue(photos_slug.strip())

            docs_page = created_docs[0].read_text(encoding="utf-8")
            photos_page = created_photos[0].read_text(encoding="utf-8")
            self.assertIn(str(docs), docs_page)
            self.assertIn(str(photos), photos_page)
            # Second run must not have clobbered the first folder's page.
            self.assertIn("a.txt", (wiki_path / "local" / docs_slug / "index.md").read_text(encoding="utf-8"))
            self.assertIn("b.txt", (wiki_path / "local" / photos_slug / "index.md").read_text(encoding="utf-8"))

    def test_rerun_of_same_folder_reuses_slug_via_marker(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            folder = home / "myproject"
            folder.mkdir()
            (folder / "a.txt").write_text("x", encoding="utf-8")

            created_first = build_local_index(home, wiki_path, [folder], "ko")
            slug_first = created_first[0].parent.name

            created_second = build_local_index(home, wiki_path, [folder], "ko")
            slug_second = created_second[0].parent.name

            self.assertEqual(slug_first, slug_second)

    def test_slug_collision_across_runs_gets_suffix_not_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            first_folder = home / "sub1" / "shared"
            first_folder.mkdir(parents=True)
            (first_folder / "first.txt").write_text("x", encoding="utf-8")

            second_folder = home / "sub2" / "shared"
            second_folder.mkdir(parents=True)
            (second_folder / "second.txt").write_text("x", encoding="utf-8")

            created_first = build_local_index(home, wiki_path, [first_folder], "ko")
            created_second = build_local_index(home, wiki_path, [second_folder], "ko")

            slug_first = created_first[0].parent.name
            slug_second = created_second[0].parent.name
            self.assertNotEqual(slug_first, slug_second)

            first_text = (wiki_path / "local" / slug_first / "index.md").read_text(encoding="utf-8")
            second_text = (wiki_path / "local" / slug_second / "index.md").read_text(encoding="utf-8")
            self.assertIn("first.txt", first_text)
            self.assertIn("second.txt", second_text)

    def test_git_subfolder_permission_error_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            folder = home / "myproject"
            (folder / "ok-sub" / ".git").mkdir(parents=True)
            (folder / "bad-sub").mkdir(parents=True)

            real_exists = Path.exists

            def flaky_exists(self):
                if self.name == ".git" and self.parent.name == "bad-sub":
                    raise PermissionError("no access")
                return real_exists(self)

            with mock.patch.object(Path, "exists", flaky_exists):
                created = build_local_index(home, wiki_path, [folder], "ko")

            self.assertTrue(created)
            text = created[0].read_text(encoding="utf-8")
            self.assertIn("ok-sub", text)

    def test_readme_permission_error_is_skipped_not_fatal_and_next_folder_still_indexed(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            folder1 = home / "proj1"
            folder1.mkdir()
            (folder1 / "README.md").write_text("hello there", encoding="utf-8")
            folder2 = home / "proj2"
            folder2.mkdir()
            (folder2 / "a.txt").write_text("x", encoding="utf-8")

            real_exists = Path.exists

            def flaky_exists(self):
                # macOS's filesystem is case-insensitive, so README.md,
                # README and readme.md all resolve to the same file: guard
                # every README-name probe under proj1, not just the first.
                if self.name.lower() in ("readme.md", "readme") and self.parent.name == "proj1":
                    raise PermissionError("no access")
                return real_exists(self)

            with mock.patch.object(Path, "exists", flaky_exists):
                created = build_local_index(home, wiki_path, [folder1, folder2], "ko")

            self.assertEqual(len(created), 2)
            text1 = created[0].read_text(encoding="utf-8")
            self.assertNotIn("## README", text1)
            text2 = created[1].read_text(encoding="utf-8")
            self.assertIn(str(folder2), text2)
            self.assertIn("a.txt", text2)

    def test_marker_after_front_matter_or_bom_is_still_recognised_on_next_run(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            folder = home / "myproject"
            folder.mkdir()
            (folder / "a.txt").write_text("x", encoding="utf-8")

            page_dir = wiki_path / "local" / "myproject"
            page_dir.mkdir(parents=True)
            # build_local_index resolves the folder (safe_under) before
            # computing its marker, so match that here too.
            marker = _marker(folder.resolve())
            existing_text = "﻿---\ntitle: x\n---\n" + marker + "\n# myproject\n"
            (page_dir / "index.md").write_text(existing_text, encoding="utf-8")

            created = build_local_index(home, wiki_path, [folder], "ko")
            slug = created[0].parent.name
            self.assertEqual(slug, "myproject")

            wiki_index = (wiki_path / "index.md").read_text(encoding="utf-8")
            self.assertEqual(wiki_index.count("local/myproject/index"), 1)

    def test_same_folder_twice_in_one_list_produces_one_page(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            wiki_path = home / "llm-wiki"
            init_wiki(home, wiki_path, "ko")

            folder = home / "myproject"
            folder.mkdir()
            (folder / "a.txt").write_text("x", encoding="utf-8")

            created = build_local_index(home, wiki_path, [folder, folder], "ko")
            self.assertEqual(len(created), 1)

            wiki_index = (wiki_path / "index.md").read_text(encoding="utf-8")
            self.assertEqual(wiki_index.count("local/myproject/index"), 1)


class MarkerHelperTest(unittest.TestCase):
    def test_marker_helper_builds_expected_text(self):
        folder = Path("/tmp/whatever")
        self.assertEqual(_marker(folder), f"<!-- local: {folder} -->")


class TreeAndGitSubfoldersPermissionTest(unittest.TestCase):
    def test_build_tree_lines_swallows_is_symlink_permission_error(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "ok").mkdir()
            bad = folder / "bad"
            bad.mkdir()

            real_is_symlink = Path.is_symlink

            def flaky_is_symlink(self):
                if self.name == "bad":
                    raise PermissionError("no access")
                return real_is_symlink(self)

            with mock.patch.object(Path, "is_symlink", flaky_is_symlink):
                lines = _build_tree_lines(folder)
            self.assertTrue(any("ok" in line for line in lines))

    def test_find_git_subfolders_swallows_is_dir_permission_error(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            (folder / "ok-sub" / ".git").mkdir(parents=True)
            (folder / "bad-sub").mkdir()

            real_is_dir = Path.is_dir

            def flaky_is_dir(self):
                if self.name == "bad-sub":
                    raise PermissionError("no access")
                return real_is_dir(self)

            with mock.patch.object(Path, "is_dir", flaky_is_dir):
                result = _find_git_subfolders(folder)
            names = [p.name for p in result]
            self.assertIn("ok-sub", names)
            self.assertNotIn("bad-sub", names)


class GitBranchesTest(unittest.TestCase):
    def test_not_a_git_repo_raises_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "plain"
            path.mkdir()
            with self.assertRaises(ValueError):
                git_branches(path)

    def test_git_probe_permission_error_raises_value_error_not_permission_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "plain"
            path.mkdir()

            real_exists = Path.exists

            def flaky_exists(self):
                if self.name == ".git":
                    raise PermissionError("no access")
                return real_exists(self)

            with mock.patch.object(Path, "exists", flaky_exists):
                with self.assertRaises(ValueError):
                    git_branches(path)

    def test_main_present_is_default(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "init", "-q", "-b", "main"],
                cwd=str(repo),
                capture_output=True,
                check=True,
            )
            commit_env = dict(os.environ)
            commit_env.update(
                {
                    "GIT_AUTHOR_NAME": "Test",
                    "GIT_AUTHOR_EMAIL": "t@test.local",
                    "GIT_COMMITTER_NAME": "Test",
                    "GIT_COMMITTER_EMAIL": "t@test.local",
                }
            )
            (repo / "README.md").write_text("hi", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, check=True)
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
                cwd=str(repo),
                env=commit_env,
                capture_output=True,
                check=True,
            )
            subprocess.run(["git", "branch", "feature"], cwd=str(repo), capture_output=True, check=True)

            result = git_branches(repo)
            self.assertEqual(result["default"], "main")
            self.assertIn("main", result["branches"])
            self.assertIn("feature", result["branches"])

    def test_master_used_when_no_main(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "init", "-q", "-b", "master"],
                cwd=str(repo),
                capture_output=True,
                check=True,
            )

            result = git_branches(repo)
            self.assertEqual(result["default"], "master")

    def test_current_branch_used_when_neither_main_nor_master(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(
                ["git", "-c", "commit.gpgsign=false", "init", "-q", "-b", "trunk"],
                cwd=str(repo),
                capture_output=True,
                check=True,
            )

            result = git_branches(repo)
            self.assertEqual(result["default"], "trunk")


if __name__ == "__main__":
    unittest.main()
