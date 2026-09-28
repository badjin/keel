from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from kit.workflow import close, docs, fsutil, work


class CloseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.home = root / "home"
        self.kb = root / "kb"
        self.folder = self.kb / "raw" / "work" / "2026-09-28-example"
        (self.folder / "records").mkdir(parents=True)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Tester")
        self.git("config", "user.email", "tester@example.com")
        (self.repo / "file.txt").write_text("first\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "phase 1")
        first = self.git("rev-parse", "HEAD")
        (self.repo / "file.txt").write_text("second\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "phase 2")
        second = self.git("rev-parse", "HEAD")
        self.intent = "# Intent\n\n## Summary\nBuild a safe workflow. Keep records.\n\n## Status\n`approved`\n\n## Changelog\n"
        self.spec = "# Spec\n\n## Acceptance Scenarios\n- **S1**: Start work\n- S2: Finish work\n"
        self.plan = "---\nkeel_plan: 1\n---\n# Plan\n\n## Phase 1: Build\n- [x] Build\n\n## Phase 2: Finish\n- [x] Finish\n"
        (self.folder / "intent.md").write_text(self.intent, encoding="utf-8")
        (self.folder / "spec.md").write_text(self.spec, encoding="utf-8")
        (self.folder / "plan.md").write_text(self.plan, encoding="utf-8")
        ledger = self.home / "state" / "workflow" / "approvals.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text(json.dumps({"folder": str(self.folder.resolve()), "hash": docs.intent_hash(self.intent)}) + "\n", encoding="utf-8")
        self.state = {
            "id": self.folder.name, "created": "2026-09-28", "lang": "en",
            "reviews": {
                "intent": {"verdict": "PASS", "hashes": {"intent": docs.intent_hash(self.intent)}},
                "spec": {"verdict": "PASS", "hashes": {"spec": docs.spec_hash(self.spec)},
                         "scenarios": ["**S1**: Start work", "S2: Finish work"]},
                "plan": {"verdict": "PASS", "hashes": {"plan": docs.plan_hash(self.plan)}},
            },
            "run": {"repo": str(self.repo), "phases": {}},
        }
        for n, commit in ((1, first), (2, second)):
            tests = self.folder / "records" / f"phase-{n}-tests.md"
            review = self.folder / "records" / f"phase-{n}-review.md"
            tests.write_text(f"# Phase {n} tests\n- Overall: PASS\n", encoding="utf-8")
            review.write_text("# Review\n- Verdict: PASS\n", encoding="utf-8")
            self.state["run"]["phases"][str(n)] = {
                "status": "done", "commit": commit,
                "tests_record": str(tests), "review_record": str(review),
            }
        self.save_state()

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args],
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def save_state(self):
        fsutil.atomic_write_json(self.folder / "state.json", self.state)

    def verify(self):
        return close.verify(self.folder, {
            "S1": {"result": "PASS", "evidence": "Opened screen"},
            "S2": {"result": "PASS", "evidence": "Saved record"},
        }, False)

    def test_verify_rejects_missing_and_extra_ids(self):
        with self.assertRaisesRegex(close.CloseError, "S2"):
            close.verify(self.folder, {"S1": {"result": "PASS", "evidence": "screen"}}, False)
        with self.assertRaisesRegex(close.CloseError, "S3"):
            close.verify(self.folder, {
                "S1": {"result": "PASS", "evidence": "screen"},
                "S2": {"result": "PASS", "evidence": "record"},
                "S3": {"result": "PASS", "evidence": "extra"},
            }, False)

    def test_audit_ignores_pass_line_in_failing_test_output(self):
        self.verify()
        tests = Path(self.state["run"]["phases"]["1"]["tests_record"])
        tests.write_text("# Tests\n- Overall: FAIL\n\n## Command\n```text\n- Overall: PASS\n```\n",
                         encoding="utf-8")
        passed, problems, _ = close.audit(self.home, self.folder)
        self.assertFalse(passed)
        self.assertEqual(len(problems), 1)

    def test_verify_records_approved_items_verbatim(self):
        record = self.verify()
        body = record.read_text(encoding="utf-8")
        self.assertIn("**S1**: Start work", body)
        self.assertIn("S2: Finish work", body)
        self.assertEqual(work.load_state(self.folder)["verification"]["ids"], ["S1", "S2"])

    def test_audit_complete_folder_closes_intent(self):
        self.verify()
        passed, problems, record = close.audit(self.home, self.folder)
        self.assertTrue(passed, problems)
        self.assertEqual(problems, [])
        self.assertTrue(record.is_file())
        self.assertEqual(docs.get_status((self.folder / "intent.md").read_text(encoding="utf-8")), "done")

    def test_audit_missing_phase_review_leaves_status(self):
        self.verify()
        Path(self.state["run"]["phases"]["1"]["review_record"]).unlink()
        passed, problems, _ = close.audit(self.home, self.folder)
        self.assertFalse(passed)
        self.assertTrue(any("phase 1" in item and "review" in item for item in problems), problems)
        self.assertEqual(docs.get_status((self.folder / "intent.md").read_text(encoding="utf-8")), "approved")

    def test_audit_no_change_phase_needs_no_review(self):
        self.verify()
        self.state = work.load_state(self.folder)
        self.state["run"]["phases"]["2"]["status"] = "no-change"
        self.state["run"]["phases"]["2"]["base"] = self.state["run"]["phases"]["2"]["commit"]
        self.state["run"]["phases"]["2"].pop("review_record")
        (self.folder / "records" / "phase-2-review.md").unlink()
        self.save_state()
        passed, problems, _ = close.audit(self.home, self.folder)
        self.assertTrue(passed, problems)

    def test_audit_rejects_no_change_with_commits(self):
        self.verify()
        current = self.state["run"]["phases"]["2"]
        current["status"] = "no-change"
        current["base"] = self.state["run"]["phases"]["1"]["commit"]
        self.save_state()
        passed, problems, record = close.audit(self.home, self.folder)
        self.assertFalse(passed)
        self.assertIn("phase 2 marked no-change but has commits", problems)
        self.assertTrue(record.is_file())

    def test_audit_records_plan_parse_error(self):
        self.verify()
        (self.folder / "plan.md").write_text("invalid\n", encoding="utf-8")
        passed, problems, record = close.audit(self.home, self.folder)
        self.assertFalse(passed)
        self.assertTrue(problems)
        self.assertTrue(record.is_file())

    def test_audit_records_approved_list_error(self):
        self.verify()
        self.state["reviews"]["spec"]["scenarios"] = ["invalid scenario"]
        self.save_state()
        passed, problems, record = close.audit(self.home, self.folder)
        self.assertFalse(passed)
        self.assertTrue(any("invalid approved scenario" in item for item in problems))
        self.assertTrue(record.is_file())

    def test_archive_is_append_only_and_idempotent(self):
        self.verify()
        close.audit(self.home, self.folder)
        index = self.kb / "index.md"
        original = "# Index\n\n## Topics\n- [[wiki/other]]\n"
        index.write_text(original, encoding="utf-8")
        self.assertEqual(close.archive(self.kb, self.folder), "archived")
        page = self.kb / "wiki" / "work-archive.md"
        first_page = page.read_bytes()
        first_index = index.read_bytes()
        self.assertEqual(first_index, (original + "- [[wiki/work-archive]]\n").encode())
        self.assertIn(b"[[raw/work/2026-09-28-example/intent]]", first_page)
        self.assertEqual(close.archive(self.kb, self.folder), "already archived")
        self.assertEqual(page.read_bytes(), first_page)
        self.assertEqual(index.read_bytes(), first_index)

        second = self.kb / "raw" / "work" / "2026-09-29-next"
        second.mkdir()
        (second / "intent.md").write_text(docs.set_status(self.intent, "done"), encoding="utf-8")
        fsutil.atomic_write_json(second / "state.json", {
            "id": second.name, "created": "2026-09-29", "lang": "en", "audit": {"passed": True},
        })
        self.assertEqual(close.archive(self.kb, second), "archived")
        self.assertTrue(page.read_bytes().startswith(first_page))
        self.assertEqual(index.read_bytes(), first_index)

    def test_archive_adds_newline_before_row(self):
        self.verify()
        close.audit(self.home, self.folder)
        page = self.kb / "wiki" / "work-archive.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Archive\n| previous |", encoding="utf-8")
        close.archive(self.kb, self.folder)
        self.assertIn("| previous |\n| 2026-09-28 |", page.read_text(encoding="utf-8"))

    def test_archive_inserts_inside_topics_before_blank(self):
        self.verify()
        close.audit(self.home, self.folder)
        index = self.kb / "index.md"
        index.write_text("# Index\n\n## Topics\n- [[wiki/other]]\n\n## Later\n", encoding="utf-8")
        close.archive(self.kb, self.folder)
        self.assertIn("- [[wiki/other]]\n- [[wiki/work-archive]]\n\n## Later", index.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
