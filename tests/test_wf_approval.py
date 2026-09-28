from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kit.workflow import approval, docs, fsutil, work


KEEL_HOME = Path(__file__).resolve().parents[1] / "kit"


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.kb = self.root / "kb"
        self.keel_home = self.root / "keel"

    def make_work(self, slug="fix-login", today="2026-09-28", status="awaiting-approval"):
        folder = work.new_work(self.kb, KEEL_HOME, slug, "en", "cli", today)
        intent = folder / "intent.md"
        content = intent.read_text(encoding="utf-8")
        content = content.replace("## Summary\n", "## Summary\nFix login.\n")
        content = content.replace("## Problem\n", "## Problem\nLogin fails.\n")
        content = content.replace("## Proposed outcome\n", "## Proposed outcome\nLogin works.\n")
        content = content.replace("## Knowledge base consulted\n", "## Knowledge base consulted\n- `wiki/login.md`\n")
        content = content.replace("## Constraints\n", "## Constraints\nKeep existing users.\n")
        content = content.replace("## Open questions\n", "## Open questions\nNone.\n")
        fsutil.atomic_write_text(intent, docs.set_status(content, status))
        state = work.load_state(folder)
        state["reviews"]["intent"] = {"verdict": "PASS", "hashes": {"intent": docs.intent_hash(intent.read_text(encoding="utf-8"))}}
        fsutil.atomic_write_json(folder / "state.json", state)
        return folder

    def ledger_lines(self):
        path = approval.ledger_path(self.keel_home)
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_match_prompt(self):
        words = ["approve"]
        self.assertEqual(approval.match_prompt("approve fix-login", words), "fix-login")
        self.assertEqual(approval.match_prompt("  Approve fix-login \n", words), "fix-login")
        self.assertIsNone(approval.match_prompt("please approve fix-login", words))
        self.assertIsNone(approval.match_prompt("approve fix-login now", words))

    def test_approve_records_hash_and_status(self):
        folder = self.make_work()
        self.assertEqual(approval.approve(self.kb, self.keel_home, "fix-login", "s1", "approve fix-login"),
                         ("approved", folder.name))
        lines = self.ledger_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["folder"], str(folder.resolve()))
        self.assertEqual(lines[0]["hash"], docs.intent_hash((folder / "intent.md").read_text(encoding="utf-8")))
        self.assertEqual(docs.get_status((folder / "intent.md").read_text(encoding="utf-8")), "approved")
        self.assertTrue(approval.is_approved(self.keel_home, folder))

    def test_ambiguous_slug_requires_full_id(self):
        first = self.make_work("x", "2026-09-27")
        self.make_work("x", "2026-09-28")
        self.assertEqual(approval.approve(self.kb, self.keel_home, "x", "s1", "approve x"), ("ambiguous", "x"))
        self.assertEqual(self.ledger_lines(), [])
        self.assertEqual(approval.approve(self.kb, self.keel_home, first.name, "s1", f"approve {first.name}"),
                         ("approved", first.name))

    def test_draft_is_not_candidate(self):
        self.make_work(status="draft")
        self.assertEqual(approval.approve(self.kb, self.keel_home, "fix-login", "s1", "approve fix-login"),
                         ("none", "fix-login"))
        self.assertEqual(self.ledger_lines(), [])

    def test_name_requires_full_slug_or_id(self):
        folder = self.make_work()
        self.assertEqual(approval.approve(self.kb, self.keel_home, "login", "s1", "approve login"), ("none", "login"))
        self.assertEqual(work.resolve(self.kb, "fix-login"), folder)
        with self.assertRaises(LookupError):
            work.resolve(self.kb, "login")
        self.assertEqual(approval.approve(self.kb, self.keel_home, folder.name, "s1", f"approve {folder.name}"),
                         ("approved", folder.name))

    def test_work_name_cannot_escape_work_root(self):
        self.make_work()
        with self.assertRaises(LookupError):
            work.resolve(self.kb, "..")

    def test_edited_reviewed_intent_is_stale(self):
        folder = self.make_work()
        intent = folder / "intent.md"
        original = intent.read_text(encoding="utf-8")
        fsutil.atomic_write_text(intent, original.replace("## Problem\n", "## Problem\nChanged.\n"))
        self.assertEqual(approval.approve(self.kb, self.keel_home, "fix-login", "s1", "approve fix-login"),
                         ("stale", folder.name))
        self.assertEqual(self.ledger_lines(), [])
        self.assertEqual(docs.get_status(intent.read_text(encoding="utf-8")), "awaiting-approval")

    def test_status_without_ledger_is_not_approved(self):
        folder = self.make_work(status="approved")
        self.assertFalse(approval.is_approved(self.keel_home, folder))

    def test_hand_edited_approved_receives_typed_approval(self):
        folder = self.make_work(status="approved")
        self.assertEqual(approval.approve(self.kb, self.keel_home, "fix-login", "s1", "approve fix-login"),
                         ("approved", folder.name))
        intent = (folder / "intent.md").read_text(encoding="utf-8")
        self.assertEqual(docs.get_status(intent), "approved")
        self.assertEqual(len(self.ledger_lines()), 1)
        self.assertEqual(self.ledger_lines()[0]["hash"], docs.intent_hash(intent))
        changelog = docs.sections(intent)["Changelog"].splitlines()
        self.assertEqual(len([line for line in changelog if line.startswith("- ")]), 2)
        self.assertIn(docs.intent_hash(intent)[:12], changelog[-1])

    def test_current_ledger_approval_is_not_candidate(self):
        folder = self.make_work()
        self.assertEqual(approval.approve(self.kb, self.keel_home, "fix-login", "s1", "approve fix-login"),
                         ("approved", folder.name))
        self.assertEqual(approval.approve(self.kb, self.keel_home, "fix-login", "s2", "approve fix-login"),
                         ("none", "fix-login"))
        self.assertEqual(len(self.ledger_lines()), 1)

    def test_hand_edited_approved_with_bad_format_is_stale(self):
        folder = self.make_work(status="approved")
        intent = folder / "intent.md"
        fsutil.atomic_write_text(intent, intent.read_text(encoding="utf-8").replace("## Summary\nFix login.", "## Summary\n"))
        state = work.load_state(folder)
        state["reviews"]["intent"]["hashes"]["intent"] = docs.intent_hash(intent.read_text(encoding="utf-8"))
        fsutil.atomic_write_json(folder / "state.json", state)
        self.assertEqual(approval.approve(self.kb, self.keel_home, "fix-login", "s1", "approve fix-login"),
                         ("stale", folder.name))
        self.assertEqual(self.ledger_lines(), [])

    def test_content_edit_invalidates_approval_but_changelog_does_not(self):
        folder = self.make_work()
        intent = folder / "intent.md"
        fsutil.atomic_write_text(intent, intent.read_text(encoding="utf-8").replace("## Problem\n", "## Problem\nLogin fails.\n"))
        state = work.load_state(folder)
        state["reviews"]["intent"]["hashes"]["intent"] = docs.intent_hash(intent.read_text(encoding="utf-8"))
        fsutil.atomic_write_json(folder / "state.json", state)
        approval.approve(self.kb, self.keel_home, folder.name, "s1", f"approve {folder.name}")
        original = intent.read_text(encoding="utf-8")
        fsutil.atomic_write_text(intent, docs.add_changelog(original, "Later note."))
        self.assertTrue(approval.is_approved(self.keel_home, folder))
        fsutil.atomic_write_text(intent, intent.read_text(encoding="utf-8").replace("Login fails.", "Login succeeds."))
        self.assertFalse(approval.is_approved(self.keel_home, folder))

    def test_add_session_is_unique_and_ordered(self):
        folder = self.make_work()
        self.assertTrue(work.add_session(folder, "s1", "codex", "/one"))
        self.assertFalse(work.add_session(folder, "s1", "codex", "/other"))
        self.assertTrue(work.add_session(folder, "s2", "claude", "/two"))
        sessions = work.load_state(folder)["sessions"]
        self.assertEqual([entry["session"] for entry in sessions], ["s1", "s2"])
        self.assertEqual([entry["transcript_path"] for entry in sessions], ["/one", "/two"])


if __name__ == "__main__":
    unittest.main()
