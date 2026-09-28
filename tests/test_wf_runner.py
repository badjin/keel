from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kit.workflow import docs, fsutil, runner, work


PLAN = """---
keel_plan: 1
---
# Plan

## Phase 1: Build
### Tests
- Run: `{test}`
- [ ] Build it

## Phase 2: Verify
### Tests
- Run: `python3 -c "pass"`
- [ ] Verify it
"""


class RunnerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.home = root / "home"
        self.kb = root / "kb"
        self.folder = self.kb / "raw" / "work" / "2026-09-28-example"
        self.folder.mkdir(parents=True)
        (self.folder / "records").mkdir()
        self.repo = root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Tester")
        self.git("config", "user.email", "tester@example.com")
        (self.repo / "file.txt").write_text("before\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "initial")
        self.initial = self.git("rev-parse", "HEAD")
        (self.folder / "intent.md").write_text("## Status\n`approved`\n")
        (self.folder / "spec.md").write_text("# Spec\n")
        self.write_plan('python3 -c "pass"')
        self.state = {
            "slug": "example",
            "reviews": {
                "spec": {"verdict": "PASS", "hashes": {"spec": docs.spec_hash("# Spec\n")}},
                "plan": {"verdict": "PASS", "hashes": {"plan": docs.plan_hash((self.folder / "plan.md").read_text())}},
            },
        }
        self.save_state()

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def save_state(self):
        fsutil.atomic_write_json(self.folder / "state.json", self.state)

    def write_plan(self, command):
        (self.folder / "plan.md").write_text(PLAN.format(test=command))

    def start(self):
        with patch("kit.workflow.runner.approval.is_approved", return_value=True):
            return runner.start(self.home, self.kb, self.folder, self.repo)

    def test_start_requires_approval_then_creates_branch(self):
        with patch("kit.workflow.runner.approval.is_approved", return_value=False):
            with self.assertRaises(runner.RunError):
                runner.start(self.home, self.kb, self.folder, self.repo)
        run = self.start()
        self.assertEqual(run["branch"], "keel/example")
        self.assertEqual(run["base"], self.initial)
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"), "keel/example")

    def test_start_rejects_untracked_file(self):
        (self.repo / "new.txt").write_text("new\n")
        with self.assertRaises(runner.RunError):
            self.start()

    def test_start_rejects_kb_inside_repo(self):
        inside = self.repo / "kb"
        with self.assertRaises(runner.RunError):
            with patch("kit.workflow.runner.approval.is_approved", return_value=True):
                runner.start(self.home, inside, self.folder, self.repo)
        self.assertNotIn("run", work.load_state(self.folder))

    def test_second_start_preserves_state(self):
        self.start()
        before = (self.folder / "state.json").read_bytes()
        with self.assertRaisesRegex(runner.RunError, "run already started"):
            self.start()
        self.assertEqual((self.folder / "state.json").read_bytes(), before)

    def test_failing_tests_leave_work_untouched(self):
        self.write_plan('python3 -c "import sys; sys.exit(1)"')
        self.state["reviews"]["plan"]["hashes"]["plan"] = docs.plan_hash((self.folder / "plan.md").read_text())
        self.save_state()
        self.start()
        (self.repo / "file.txt").write_text("after\n")
        result = runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))
        self.assertEqual(result["status"], "tests-failed")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertTrue(self.git("status", "--porcelain"))
        self.assertIn("- [ ] Build it", (self.folder / "plan.md").read_text())

    def review(self, verdict):
        return lambda *args, **kwargs: (verdict, self.folder / "records" / "review.md", [], "")

    def test_passing_phase_commits_and_ticks(self):
        self.start()
        (self.repo / "file.txt").write_text("after\n")
        result = runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.git("log", "-1", "--format=%s"), "keel: phase 1 — Build")
        self.assertIn("- [x] Build it", (self.folder / "plan.md").read_text())
        self.assertEqual(work.load_state(self.folder)["run"]["phases"]["1"]["status"], "done")

    def test_review_failure_keeps_base_for_retry(self):
        self.start()
        (self.repo / "file.txt").write_text("after\n")
        result = runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("FAIL"))
        self.assertEqual(result["status"], "review-failed")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertIn("- [ ] Build it", (self.folder / "plan.md").read_text())
        (self.repo / "file.txt").write_text("retry\n")
        runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))
        self.assertEqual(work.load_state(self.folder)["run"]["phases"]["1"]["base"], self.initial)

    def _assert_review_retry_with_no_changes(self, first):
        self.start()
        (self.repo / "file.txt").write_text("after\n")
        calls = []

        def review(*args, **kwargs):
            calls.append(kwargs)
            saved = work.load_state(self.folder)["run"]["phases"]["1"]
            self.assertEqual(saved["commit"], self.git("rev-parse", "HEAD"))
            verdict = first if len(calls) == 1 else "PASS"
            return verdict, self.folder / "records" / "review.md", [], ""

        first_result = runner.run_phase(self.home, self.kb, self.folder, 1, review=review)
        self.assertEqual(first_result["status"], "review-failed")
        self.assertIn("- [ ] Build it", (self.folder / "plan.md").read_text())
        commit = self.git("rev-parse", "HEAD")
        second_result = runner.run_phase(self.home, self.kb, self.folder, 1,
                                         extra_review=True, review=review)
        self.assertEqual(second_result["status"], "done")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.git("rev-parse", "HEAD"), commit)
        self.assertEqual(second_result["commit"], commit)
        self.assertEqual(calls[1]["tests_record"], Path(second_result["tests_record"]))

    def test_failed_review_retry_with_no_changes(self):
        self._assert_review_retry_with_no_changes("FAIL")

    def test_errored_review_retry_with_no_changes(self):
        self._assert_review_retry_with_no_changes("ERROR")

    def test_external_commit_is_reviewed(self):
        self.start()
        (self.repo / "file.txt").write_text("external\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "external")
        commit = self.git("rev-parse", "HEAD")
        result = runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["commit"], commit)

    def test_external_secret_commit_is_rejected(self):
        self.start()
        (self.repo / ".env").write_text("secret\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "external secret")
        with self.assertRaises(runner.RunError):
            runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))
        self.assertIn("- [ ] Build it", (self.folder / "plan.md").read_text())

    def test_completed_phase_cannot_rerun(self):
        self.start()
        for status in ("done", "no-change"):
            self.state = work.load_state(self.folder)
            self.state["run"]["phases"]["1"] = {"status": status, "base": self.initial,
                                                  "commit": self.initial}
            self.save_state()
            with self.subTest(status=status), self.assertRaises(runner.RunError):
                runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))

    def test_earlier_phase_cannot_run_after_later_phase_has_state(self):
        self.start()
        self.state = work.load_state(self.folder)
        self.state["run"]["phases"]["2"] = {"status": "tests-failed"}
        self.save_state()
        with self.assertRaises(runner.RunError):
            runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))

    def test_secret_file_resets_index(self):
        self.start()
        (self.repo / ".env").write_text("secret\n")
        with self.assertRaises(runner.RunError):
            runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))
        self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_unicode_secret_file_resets_index(self):
        self.start()
        secret = self.repo / "한글" / ".env"
        secret.parent.mkdir()
        secret.write_text("secret\n")
        with self.assertRaises(runner.RunError):
            runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_latin1_test_output_is_recorded(self):
        self.write_plan('python3 -c "import sys; sys.stdout.buffer.write(bytes([233]))"')
        self.state["reviews"]["plan"]["hashes"]["plan"] = docs.plan_hash((self.folder / "plan.md").read_text())
        self.save_state()
        self.start()
        result = runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))
        self.assertTrue(Path(result["tests_record"]).is_file())

    def test_no_change_ticks_without_commit(self):
        self.start()
        result = runner.run_phase(self.home, self.kb, self.folder, 1, review=self.review("PASS"))
        self.assertEqual(result["status"], "no-change")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertIn("- [x] Build it", (self.folder / "plan.md").read_text())

    def test_phase_two_requires_phase_one(self):
        self.start()
        with self.assertRaises(runner.RunError):
            runner.run_phase(self.home, self.kb, self.folder, 2, review=self.review("PASS"))


if __name__ == "__main__":
    unittest.main()
