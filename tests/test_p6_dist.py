from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
MAKE_DIST = ROOT / "scripts" / "make_dist.sh"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _git_available() -> bool:
    return shutil.which("git") is not None


def _init_fixture_repo(root: Path, extra_files: Optional[dict] = None) -> Path:
    """A minimal, throwaway git repo shaped like this kit, so the dist
    build/scan test does not depend on this repo's own HEAD or commit
    state (new Phase 6 files are not committed yet when this test runs)."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (root / ".gitattributes").write_text("DOCS export-ignore\n", encoding="utf-8")
    (root / "INSTALL.md").write_text(
        "Read app/server.py and poll install-result.json.\n", encoding="utf-8"
    )
    (root / "app").mkdir()
    (root / "app" / "server.py").write_text("# server\n", encoding="utf-8")
    (root / "DOCS").mkdir()
    (root / "DOCS" / "notes.md").write_text("internal planning notes\n", encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
    return root


@unittest.skipUnless(_git_available(), "git not available")
class MakeDistScriptTest(unittest.TestCase):
    """Exercises scripts/make_dist.sh against an isolated fixture repo,
    not this repo's own HEAD, so the test passes regardless of whether
    Phase 6's own files have been committed yet."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="make-dist-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_builds_zip_excluding_docs(self) -> None:
        repo = _init_fixture_repo(self.tmp / "repo")
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        zip_path = repo / "dist" / "keel-0.1.0.zip"
        self.assertTrue(zip_path.exists(), f"{zip_path} was not created")

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()

        self.assertIn("keel-0.1.0/INSTALL.md", names)
        self.assertIn("keel-0.1.0/app/server.py", names)
        self.assertFalse(
            [n for n in names if n.startswith("keel-0.1.0/DOCS/")],
            "dist zip must not contain DOCS/ entries",
        )

        install_md_path = repo / "dist" / "INSTALL.md"
        self.assertTrue(install_md_path.exists(), "dist/INSTALL.md was not created")
        self.assertEqual(
            install_md_path.read_text(encoding="utf-8"),
            (repo / "INSTALL.md").read_text(encoding="utf-8"),
        )
        self.assertFalse((repo / "dist" / "README.txt").exists(), "dist/README.txt must not be created")

    def test_stale_dist_files_are_removed_leaving_only_zip_and_install_md(self) -> None:
        repo = _init_fixture_repo(self.tmp / "repo")
        stale_dir = repo / "dist"
        stale_dir.mkdir()
        (stale_dir / "README.txt").write_text("old readme\n", encoding="utf-8")
        (stale_dir / "keel-0.0.1.zip").write_bytes(b"stale-zip")

        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        entries = sorted(p.name for p in (repo / "dist").iterdir())
        self.assertEqual(entries, ["INSTALL.md", "keel-0.1.0.zip"])

    def test_dist_install_md_comes_from_head_not_dirty_working_tree(self) -> None:
        # Task 6.17: dist/INSTALL.md must be identical to the one inside
        # the zip (both read from `git show HEAD:INSTALL.md`), not copied
        # from a possibly-dirty working tree.
        repo = _init_fixture_repo(self.tmp / "repo")
        committed_text = (repo / "INSTALL.md").read_text(encoding="utf-8")
        (repo / "INSTALL.md").write_text(
            "Uncommitted local edit that must not ship.\n", encoding="utf-8"
        )

        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        dist_install_md = (repo / "dist" / "INSTALL.md").read_text(encoding="utf-8")
        self.assertEqual(dist_install_md, committed_text)
        self.assertNotIn("Uncommitted local edit", dist_install_md)

        with zipfile.ZipFile(repo / "dist" / "keel-0.1.0.zip") as zf:
            zipped_install_md = zf.read("keel-0.1.0/INSTALL.md").decode("utf-8")
        self.assertEqual(dist_install_md, zipped_install_md)

    def test_fails_on_leaked_github_token(self) -> None:
        fake_token = "ghp_" + "x" * 36
        repo = _init_fixture_repo(
            self.tmp / "repo-secret",
            extra_files={"app/leftover.py": f"TOKEN = '{fake_token}'\n"},
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_fails_on_author_home_path(self) -> None:
        author_home = "/Users/" + "jin" + "kim"
        repo = _init_fixture_repo(
            self.tmp / "repo-home",
            extra_files={"app/leftover.py": f"# built on {author_home}/Sites\n"},
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_fails_on_leaked_secret_first_line_of_a_large_file(self) -> None:
        fake_token = "ghp_" + "x" * 36
        big_padding = "x" * (150 * 1024)
        repo = _init_fixture_repo(
            self.tmp / "repo-big",
            extra_files={"app/big.txt": f"TOKEN={fake_token}\n{big_padding}\n"},
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_fails_on_leaked_github_pat(self) -> None:
        fake_token = "github_pat_" + "x" * 82
        repo = _init_fixture_repo(
            self.tmp / "repo-pat",
            extra_files={"app/leftover.py": f"TOKEN = '{fake_token}'\n"},
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_fails_on_leaked_slack_token(self) -> None:
        fake_token = "xoxb-" + "1234567890"
        repo = _init_fixture_repo(
            self.tmp / "repo-slack",
            extra_files={"app/leftover.py": f"TOKEN = '{fake_token}'\n"},
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_fails_on_leaked_atlassian_token(self) -> None:
        fake_token = "ATATT" + "x" * 25
        repo = _init_fixture_repo(
            self.tmp / "repo-atlassian",
            extra_files={"app/leftover.py": f"TOKEN = '{fake_token}'\n"},
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_fails_on_leaked_aws_key(self) -> None:
        fake_token = "AKIA" + "X" * 16
        repo = _init_fixture_repo(
            self.tmp / "repo-aws",
            extra_files={"app/leftover.py": f"TOKEN = '{fake_token}'\n"},
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_fails_on_token_in_bracketed_filename(self) -> None:
        fake_token = "ghp_" + "y" * 36
        repo = _init_fixture_repo(
            self.tmp / "repo-bracket",
            extra_files={"app/a[1].txt": f"TOKEN={fake_token}\n"},
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_passes_when_real_make_dist_script_is_shipped_in_the_repo(self) -> None:
        # The real script uses length-qualified patterns and a split author-home
        # string precisely so that shipping it inside the dist zip never makes
        # the scan match itself.
        repo = _init_fixture_repo(self.tmp / "repo-self")
        real_script = (repo / "scripts" / "make_dist.sh")
        real_script.parent.mkdir(parents=True, exist_ok=True)
        real_script.write_text(MAKE_DIST.read_text(encoding="utf-8"), encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "add real make_dist.sh")

        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(_git_available(), "git not available")
class MakeDistLeanContentsTest(unittest.TestCase):
    """Task 6.5 step 6: tests/ and scripts/ are export-ignored so the
    distributed zip ships only what the end user or agent actually runs,
    while user-facing files stay in the archive."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="make-dist-lean-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_zip_excludes_tests_and_scripts_but_keeps_user_files(self) -> None:
        repo = _init_fixture_repo(
            self.tmp / "repo-lean",
            extra_files={
                ".gitattributes": (ROOT / ".gitattributes").read_text(encoding="utf-8"),
                "README.md": "readme\n",
                "kit/install_hooks.py": "# install_hooks\n",
                "tests/test_something.py": "def test_x():\n    assert True\n",
                "scripts/make_dist.sh": MAKE_DIST.read_text(encoding="utf-8"),
            },
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        zip_path = repo / "dist" / "keel-0.1.0.zip"
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()

        self.assertFalse(
            [n for n in names if n.startswith("keel-0.1.0/tests/")],
            "dist zip must not contain tests/ entries",
        )
        self.assertFalse(
            [n for n in names if n.startswith("keel-0.1.0/scripts/")],
            "dist zip must not contain scripts/ entries",
        )
        self.assertIn("keel-0.1.0/INSTALL.md", names)
        self.assertIn("keel-0.1.0/README.md", names)
        self.assertIn("keel-0.1.0/VERSION", names)
        self.assertIn("keel-0.1.0/app/server.py", names)
        self.assertIn("keel-0.1.0/kit/install_hooks.py", names)

    def test_nested_tests_dir_stays_while_top_level_tests_is_dropped(self) -> None:
        """Task 6.7 step 5: /tests export-ignore must be root-anchored —
        a nested kit/x/tests/a.py is a package fixture, not this repo's
        own test suite, and must survive in the shipped archive."""
        repo = _init_fixture_repo(
            self.tmp / "repo-lean-nested",
            extra_files={
                ".gitattributes": (ROOT / ".gitattributes").read_text(encoding="utf-8"),
                "README.md": "readme\n",
                "kit/install_hooks.py": "# install_hooks\n",
                "tests/test_something.py": "def test_x():\n    assert True\n",
                "kit/x/tests/a.py": "# nested fixture, not this repo's test suite\n",
                "scripts/make_dist.sh": MAKE_DIST.read_text(encoding="utf-8"),
            },
        )
        result = subprocess.run(
            ["bash", str(MAKE_DIST)], cwd=repo, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        zip_path = repo / "dist" / "keel-0.1.0.zip"
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()

        self.assertFalse(
            [n for n in names if n.startswith("keel-0.1.0/tests/")],
            "dist zip must not contain top-level tests/ entries",
        )
        self.assertIn(
            "keel-0.1.0/kit/x/tests/a.py",
            names,
            "dist zip must keep a nested kit/x/tests/ fixture file",
        )


class GitattributesAnchoredTest(unittest.TestCase):
    """Task 6.6 step 6: /tests and /scripts export-ignore lines are
    anchored to the repo root."""

    def test_tests_and_scripts_lines_are_anchored(self) -> None:
        lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        self.assertIn("/tests export-ignore", lines)
        self.assertIn("/scripts export-ignore", lines)
        self.assertNotIn("tests export-ignore", lines)
        self.assertNotIn("scripts export-ignore", lines)


class ReadmeBilingualTest(unittest.TestCase):
    """Task 6.15 step 1 docs: README.md (English) and README.ko.md
    (Korean) exist side by side with the same section structure, and each
    links to the other on its first lines."""

    def test_readme_ko_md_exists(self) -> None:
        self.assertTrue((ROOT / "README.ko.md").is_file())

    def test_heading_counts_match(self) -> None:
        en = (ROOT / "README.md").read_text(encoding="utf-8")
        ko = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        en_headings = re.findall(r"(?m)^## ", en)
        ko_headings = re.findall(r"(?m)^## ", ko)
        self.assertEqual(len(en_headings), len(ko_headings))
        self.assertGreaterEqual(len(en_headings), 5)

if __name__ == "__main__":
    unittest.main()
