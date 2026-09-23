from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unittest import mock

from kit.history import Change, collect_changes, month_of


def _run(cmd, cwd, env):
    subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, check=True)


def _git_env(base_env, author_date, committer_date):
    env = dict(base_env)
    env["GIT_AUTHOR_NAME"] = "Test Author"
    env["GIT_AUTHOR_EMAIL"] = "author@test.local"
    env["GIT_COMMITTER_NAME"] = "Test Author"
    env["GIT_COMMITTER_EMAIL"] = "author@test.local"
    env["GIT_AUTHOR_DATE"] = author_date
    env["GIT_COMMITTER_DATE"] = committer_date
    return env


class CollectChangesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        self.now = datetime(2024, 6, 15, 12, 0, 0)
        self.base_env = {"PATH": os.environ.get("PATH", "")}
        _run(["git", "-c", "commit.gpgsign=false", "init", "-q", "-b", "main"], self.repo, self.base_env)

    def _date(self, days_ago):
        dt = self.now - timedelta(days=days_ago)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    def _commit(self, files, message, days_ago):
        for name, content in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _run(["git", "add", "-A"], self.repo, self.base_env)
        d = self._date(days_ago)
        env = _git_env(self.base_env, d, d)
        _run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", message], self.repo, env)

    def _build_fixture(self):
        self._commit({"README.md": "v1"}, "Initial", 400)
        self._commit({"README.md": "v2"}, "Docs tweak", 10)

        _run(["git", "checkout", "-q", "-b", "feat"], self.repo, self.base_env)
        self._commit({"src/a.py": "a"}, "add a", 6)
        self._commit({"docs/x.md": "x"}, "add x", 5.5)
        _run(["git", "checkout", "-q", "main"], self.repo, self.base_env)

        d = self._date(5)
        env = _git_env(self.base_env, d, d)
        _run(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "merge",
                "-q",
                "--no-ff",
                "-m",
                "Merge pull request #7 from me/feat",
                "-m",
                "Add feature A",
                "feat",
            ],
            self.repo,
            env,
        )

        self._commit({"src/b.py": "b"}, "Fix bug (#9)", 2)

    def test_since_30_days(self):
        self._build_fixture()
        changes = collect_changes(self.repo, "main", since_days=30, now=self.now)

        self.assertEqual(len(changes), 3)
        subjects = [c.subject for c in changes]
        self.assertNotIn("add a", subjects)
        self.assertNotIn("add x", subjects)
        self.assertNotIn("Initial", subjects)

        squash = changes[0]
        self.assertEqual(squash.pr, 9)
        self.assertEqual(squash.title, "Fix bug")
        self.assertFalse(squash.is_merge)
        self.assertEqual(squash.areas, ["src"])

        merge = changes[1]
        self.assertEqual(merge.pr, 7)
        self.assertEqual(merge.title, "Add feature A")
        self.assertTrue(merge.is_merge)
        self.assertEqual(merge.areas, ["docs", "src"])

        direct = changes[2]
        self.assertEqual(direct.subject, "Docs tweak")
        self.assertIsNone(direct.pr)
        self.assertFalse(direct.is_merge)

        self.assertEqual(month_of(merge), merge.date[:7])

    def test_since_1_day_is_empty(self):
        self._build_fixture()
        changes = collect_changes(self.repo, "main", since_days=1, now=self.now)
        self.assertEqual(changes, [])

    def test_change_is_dataclass_with_expected_fields(self):
        self._build_fixture()
        changes = collect_changes(self.repo, "main", since_days=30, now=self.now)
        c = changes[0]
        self.assertIsInstance(c, Change)
        for attr in ("sha", "is_merge", "author", "date", "subject", "title", "pr", "areas"):
            self.assertTrue(hasattr(c, attr))

    def test_body_with_blank_lines_does_not_swallow_file_list(self):
        d = self._date(3)
        env = _git_env(self.base_env, d, d)
        body = "* wip\n\n* more\n\n---------\n\nCo-authored-by: A <a@x>"
        (self.repo / "src").mkdir(parents=True, exist_ok=True)
        (self.repo / "src" / "x.py").write_text("x", encoding="utf-8")
        _run(["git", "add", "-A"], self.repo, self.base_env)
        _run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Squash merge", "-m", body],
            self.repo,
            env,
        )
        changes = collect_changes(self.repo, "main", since_days=30, now=self.now)
        self.assertEqual(changes[0].areas, ["src"])

    def test_merge_with_no_file_changes_has_empty_areas(self):
        self._commit({"README.md": "v1"}, "Initial", 20)
        _run(["git", "checkout", "-q", "-b", "empty-feat"], self.repo, self.base_env)
        d_empty = self._date(15)
        env_empty = _git_env(self.base_env, d_empty, d_empty)
        _run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "empty commit on feat"],
            self.repo,
            env_empty,
        )
        _run(["git", "checkout", "-q", "main"], self.repo, self.base_env)
        d = self._date(10)
        env = _git_env(self.base_env, d, d)
        _run(
            ["git", "-c", "commit.gpgsign=false", "merge", "-s", "ours", "-q", "--no-ff", "-m", "Merge empty", "empty-feat"],
            self.repo,
            env,
        )
        changes = collect_changes(self.repo, "main", since_days=30, now=self.now)
        merge = next(c for c in changes if c.subject == "Merge empty")
        self.assertEqual(merge.areas, [])

    def test_commit_filtered_by_absolute_instant_across_timezones(self):
        # Commit authored 2026-01-01T23:30:00-08:00 == 2026-01-02T07:30:00Z.
        # "now" is 2026-01-02T08:00:00 in a UTC+10 zone == 2026-01-01T22:00:00Z.
        # Comparing wall-clock digits (23:30 vs 08:00, ignoring offsets) instead
        # of absolute instants would misjudge the window -- and since `now` is
        # timezone-aware here, the old naive-vs-aware comparison raised
        # TypeError. The fixed code must compare instants and not raise.
        date_str = "2026-01-01T23:30:00-08:00"
        env = dict(self.base_env)
        env.update(
            {
                "GIT_AUTHOR_NAME": "Test Author",
                "GIT_AUTHOR_EMAIL": "author@test.local",
                "GIT_COMMITTER_NAME": "Test Author",
                "GIT_COMMITTER_EMAIL": "author@test.local",
                "GIT_AUTHOR_DATE": date_str,
                "GIT_COMMITTER_DATE": date_str,
            }
        )
        (self.repo / "README.md").write_text("v1", encoding="utf-8")
        _run(["git", "add", "-A"], self.repo, self.base_env)
        _run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "boundary commit"],
            self.repo,
            env,
        )

        now = datetime(2026, 1, 2, 8, 0, 0, tzinfo=timezone(timedelta(hours=10)))
        changes = collect_changes(self.repo, "main", since_days=1, now=now)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].subject, "boundary commit")

    def test_nonexistent_branch_raises_runtime_error(self):
        self._commit({"README.md": "v1"}, "Initial", 5)
        with self.assertRaises(RuntimeError):
            collect_changes(self.repo, "does-not-exist", since_days=30, now=self.now)

    def test_out_of_order_committer_dates_filtered_in_python(self):
        # C1: 10 days ago (in window). C2: 40 days ago, but positioned AFTER C1
        # in history (out of order). C3: 5 days ago (in window, newest).
        self._commit({"README.md": "v1"}, "C1 recentish", 10)
        self._commit({"README.md": "v2"}, "C2 old but later in history", 40)
        self._commit({"README.md": "v3"}, "C3 newest", 5)

        changes = collect_changes(self.repo, "main", since_days=30, now=self.now)
        subjects = {c.subject for c in changes}
        self.assertIn("C1 recentish", subjects)
        self.assertIn("C3 newest", subjects)
        self.assertNotIn("C2 old but later in history", subjects)


    def test_committer_date_with_trailing_z_parses_on_any_interpreter(self):
        from kit.history import _committer_date

        dt = _committer_date("2026-01-02T07:30:00Z")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.day, 2)

    def test_git_log_uses_since_prefilter_and_dashdash_after_branch(self):
        self._commit({"README.md": "v1"}, "Initial", 5)
        recorded = {}
        real_run = subprocess.run

        def spy_run(args, *a, **kw):
            if "log" in args:
                recorded["args"] = args
            return real_run(args, *a, **kw)

        with mock.patch("kit.history.subprocess.run", side_effect=spy_run):
            collect_changes(self.repo, "main", since_days=30, now=self.now)

        args = recorded["args"]
        self.assertTrue(any(a.startswith("--since") for a in args))
        branch_idx = args.index("main")
        self.assertEqual(args[-1], "--")
        self.assertGreater(args.index("--"), branch_idx)


if __name__ == "__main__":
    unittest.main()
