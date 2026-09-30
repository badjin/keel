from __future__ import annotations

import json
import queue
import re
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

from kit.workflow import approval, approval_page, docs, fsutil, work


KEEL_HOME = Path(__file__).resolve().parents[1] / "kit"


class ApprovalPageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.kb = self.root / "kb"
        self.keel_home = self.root / "keel"

    def make_work(self):
        folder = work.new_work(self.kb, KEEL_HOME, "fix-login", "en", "cli", "2026-09-28")
        intent = folder / "intent.md"
        content = intent.read_text(encoding="utf-8")
        content = content.replace("## Summary\n", "## Summary\nFix login.\n")
        content = content.replace("## Problem\n", "## Problem\nLogin fails.\n")
        content = content.replace("## Proposed outcome\n", "## Proposed outcome\nLogin works.\n")
        content = content.replace("## Knowledge base consulted\n", "## Knowledge base consulted\n- `wiki/login.md`\n")
        content = content.replace("## Constraints\n", "## Constraints\nKeep existing users.\n")
        content = content.replace("## Open questions\n", "## Open questions\nNone.\n")
        fsutil.atomic_write_text(intent, docs.set_status(content, "awaiting-approval"))
        state = work.load_state(folder)
        state["reviews"]["intent"] = {
            "verdict": "PASS", "hashes": {"intent": docs.intent_hash(intent.read_text(encoding="utf-8"))}
        }
        fsutil.atomic_write_json(folder / "state.json", state)
        return folder

    def ledger_lines(self):
        path = approval.ledger_path(self.keel_home)
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_approve_records_current_hash(self):
        folder = self.make_work()
        digest = docs.intent_hash((folder / "intent.md").read_text(encoding="utf-8"))
        self.assertEqual(approval_page.decide(self.kb, self.keel_home, folder, "approve", digest), "approved")
        self.assertEqual(docs.get_status((folder / "intent.md").read_text(encoding="utf-8")), "approved")
        self.assertEqual(len(self.ledger_lines()), 1)
        self.assertEqual(self.ledger_lines()[0]["hash"], digest)

    def test_approve_rejects_shown_hash_mismatch(self):
        folder = self.make_work()
        self.assertEqual(approval_page.decide(self.kb, self.keel_home, folder, "approve", "wrong"), "stale")
        self.assertEqual(docs.get_status((folder / "intent.md").read_text(encoding="utf-8")), "awaiting-approval")
        self.assertEqual(self.ledger_lines(), [])

    def test_approve_rejects_edit_after_review(self):
        folder = self.make_work()
        intent = folder / "intent.md"
        fsutil.atomic_write_text(intent, intent.read_text(encoding="utf-8").replace("Login fails.", "Login changed."))
        digest = docs.intent_hash(intent.read_text(encoding="utf-8"))
        self.assertEqual(approval_page.decide(self.kb, self.keel_home, folder, "approve", digest), "stale")
        self.assertEqual(self.ledger_lines(), [])

    def test_changes_clears_review_and_failure_count(self):
        folder = self.make_work()
        state = work.load_state(folder)
        state["review_fails"]["intent"] = 2
        fsutil.atomic_write_json(folder / "state.json", state)
        digest = docs.intent_hash((folder / "intent.md").read_text(encoding="utf-8"))
        self.assertEqual(approval_page.decide(self.kb, self.keel_home, folder, "changes", digest, "Please revise"), "changes")
        self.assertEqual(docs.get_status((folder / "intent.md").read_text(encoding="utf-8")), "draft")
        state = work.load_state(folder)
        self.assertNotIn("intent", state["reviews"])
        self.assertEqual(state["review_fails"]["intent"], 0)
        self.assertEqual(state["changes_requested"]["note"], "Please revise")
        changelog = docs.sections((folder / "intent.md").read_text(encoding="utf-8"))["Changelog"].splitlines()
        self.assertIn("Please revise", changelog[-1])
        self.assertEqual(self.ledger_lines(), [])

    def test_second_changes_returns_none(self):
        folder = self.make_work()
        digest = docs.intent_hash((folder / "intent.md").read_text(encoding="utf-8"))
        self.assertEqual(approval_page.decide(self.kb, self.keel_home, folder, "changes", digest), "changes")
        self.assertEqual(approval_page.decide(self.kb, self.keel_home, folder, "changes", digest), "none")

    def test_unknown_decision_raises(self):
        folder = self.make_work()
        digest = docs.intent_hash((folder / "intent.md").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            approval_page.decide(self.kb, self.keel_home, folder, "unknown", digest)

    def start_server(self, folder):
        addresses = queue.Queue()
        outcomes = queue.Queue()
        thread = threading.Thread(
            target=lambda: outcomes.put(approval_page.serve(
                self.kb, self.keel_home, folder, announce=addresses.put,
                open_browser=False, idle_seconds=3, poll_seconds=0.1,
            )),
            daemon=True,
        )
        thread.start()
        self.addCleanup(thread.join, 4)
        return addresses.get(timeout=1), outcomes, thread

    def test_serve_checks_requests_and_stops_after_approval(self):
        folder = self.make_work()
        url, outcomes, thread = self.start_server(folder)
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(url.replace(url.split("/")[3], "wrong"), timeout=1)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(request.Request(url, headers={"Host": "evil.example"}), timeout=1)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

        decide_url = url + "decide"
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(request.Request(
                decide_url, data=b"{}", headers={"Origin": "http://evil.example"}
            ), timeout=1)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

        oversized = json.dumps({
            "decision": "changes",
            "hash": docs.intent_hash((folder / "intent.md").read_text(encoding="utf-8")),
            "note": "x" * 9000,
        }).encode("utf-8")
        with self.assertRaises(error.HTTPError) as caught:
            request.urlopen(request.Request(decide_url, data=oversized), timeout=1)
        self.assertGreaterEqual(caught.exception.code, 400)
        self.assertLess(caught.exception.code, 500)
        caught.exception.close()
        self.assertEqual(docs.get_status((folder / "intent.md").read_text(encoding="utf-8")), "awaiting-approval")

        with request.urlopen(url, timeout=1) as response:
            self.assertEqual(response.headers["X-Keel-Approval-Page"], work.load_state(folder)["slug"])
            page = response.read().decode("utf-8")
        digest = json.loads(re.search(r"const hash = (\"[^\"]+\");", page).group(1))
        payload = json.dumps({"decision": "approve", "hash": digest}).encode("utf-8")
        with request.urlopen(request.Request(decide_url, data=payload), timeout=2) as response:
            self.assertEqual(json.load(response), {"result": "approved"})
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes.get_nowait(), "approved")
        self.assertEqual(docs.get_status((folder / "intent.md").read_text(encoding="utf-8")), "approved")
        with self.assertRaises(error.URLError):
            request.urlopen(url, timeout=1)

    def test_serve_stops_after_typed_approval(self):
        folder = self.make_work()
        _, outcomes, thread = self.start_server(folder)
        self.assertEqual(approval.approve(self.kb, self.keel_home, folder.name, "test", "approve")[0], "approved")
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes.get_nowait(), "approved")


if __name__ == "__main__":
    unittest.main()
