from __future__ import annotations

import contextlib
import http.server
import io
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from kit.workflow import approval, cli, docs, fsutil, work


SOURCE_WORKFLOW = Path(__file__).resolve().parents[1] / "kit" / "workflow"
COMPLETE_INTENT = """# Intent: example

## Summary
Complete the workflow.
## Problem
The workflow is missing.
## Proposed outcome
- Add the workflow. [Q1]
## Affected users and systems
Users and the CLI.
## Knowledge base consulted
- `index.md` — Workflow overview.
## Constraints
- Preserve existing files. [D1]
## Decisions
**D1** Preserve existing files.
## Open questions
None.
## Quotes
**Q1** Add the workflow.
## Status
`draft`
## Changelog
- Created.
"""


class WorkflowCliTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.home = root / "home"
        self.kb = root / "kb"
        shutil.copytree(SOURCE_WORKFLOW, self.home / "workflow",
                        ignore=shutil.ignore_patterns("__pycache__"))
        fsutil.atomic_write_json(self.home / "config.json", {
            "wiki_path": str(self.kb), "workflow": {"language": "en"},
        })

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli.main(list(args), keel_home=self.home)
        return result, output.getvalue()

    def reviewed_intent(self):
        self.invoke("new", "example")
        folder = work.resolve(self.kb, "example")
        intent = folder / "intent.md"
        fsutil.atomic_write_text(intent, COMPLETE_INTENT)
        state = work.load_state(folder)
        state["reviews"]["intent"] = {
            "verdict": "PASS", "hashes": {"intent": docs.intent_hash(COMPLETE_INTENT)},
        }
        fsutil.atomic_write_json(folder / "state.json", state)
        return folder, intent

    def cleanup_page(self, folder):
        ready = cli.page_ready_path(self.home, work.load_state(folder)["slug"])
        if ready.exists():
            try:
                os.kill(fsutil.read_json(ready, {})["pid"], signal.SIGTERM)
            except (OSError, KeyError):
                pass

    def test_new_template_cannot_be_submitted(self):
        result, output = self.invoke("new", "example")
        self.assertEqual(result, 0)
        folder = work.resolve(self.kb, "example")
        self.assertIn(str(folder.resolve()), output)
        self.assertIn(str((folder / "intent.md").resolve()), output)
        result, output = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 1)
        self.assertTrue(output.strip())
        self.assertEqual(docs.get_status((folder / "intent.md").read_text()), "draft")

    def test_submit_requires_current_pass_review(self):
        folder, intent = self.reviewed_intent()
        with patch.dict(os.environ, {"KEEL_NO_BROWSER": "1"}), patch(
            "kit.workflow.cli.subprocess.Popen", return_value=object()
        ), patch.object(cli, "PAGE_START_SECONDS", 0.3):
            result, output = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 0)
        self.assertEqual(docs.get_status(intent.read_text()), "awaiting-approval")
        self.assertIn("approve example", output)
        self.assertIn(str(intent.resolve()), output)
        self.assertIn("review --work example", output)
        self.assertNotIn("Approval page:", output)

        fsutil.atomic_write_text(intent, COMPLETE_INTENT.replace("missing", "incomplete"))
        result, _ = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 1)

    def test_submit_falls_back_when_spawn_raises(self):
        _, intent = self.reviewed_intent()
        with patch.dict(os.environ, {"KEEL_NO_BROWSER": "1"}), patch(
            "kit.workflow.cli.subprocess.Popen", side_effect=OSError("spawn failed")
        ):
            result, output = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 0)
        self.assertEqual(docs.get_status(intent.read_text()), "awaiting-approval")
        self.assertIn("approve example", output)
        self.assertIn("review --work example", output)
        self.assertNotIn("Approval page:", output)

    def test_submit_falls_back_when_page_start_raises(self):
        _, intent = self.reviewed_intent()
        with patch.dict(os.environ, {"KEEL_NO_BROWSER": "1"}), patch.object(
            cli, "_start_page", side_effect=LookupError("ready file")
        ):
            result, output = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 0)
        self.assertEqual(docs.get_status(intent.read_text()), "awaiting-approval")
        self.assertIn("approve example", output)
        self.assertIn("review --work example", output)
        self.assertNotIn("Approval page:", output)

    def test_submit_falls_back_when_child_cannot_bind(self):
        folder, intent = self.reviewed_intent()
        self.addCleanup(self.cleanup_page, folder)
        with patch.dict(os.environ, {"KEEL_NO_BROWSER": "1", "KEEL_PAGE_FORCE_BIND_FAIL": "1"}), patch.object(
            cli, "PAGE_START_SECONDS", 3
        ):
            result, output = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 0)
        self.assertEqual(docs.get_status(intent.read_text()), "awaiting-approval")
        self.assertIn("approve example", output)
        self.assertIn("review --work example", output)
        self.assertNotIn("Approval page:", output)

    def test_submit_starts_page_and_page_exits_after_approval(self):
        folder, intent = self.reviewed_intent()
        self.addCleanup(self.cleanup_page, folder)
        with patch.dict(os.environ, {"KEEL_NO_BROWSER": "1"}):
            result, output = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 0)
        self.assertEqual(docs.get_status(intent.read_text()), "awaiting-approval")
        self.assertIn("approve example", output)
        self.assertIn("Approval page: http://127.0.0.1:", output)
        url = fsutil.read_json(cli.page_ready_path(self.home, "example"), {})["url"]
        with urllib.request.urlopen(url, timeout=1) as reply:
            self.assertEqual(reply.status, 200)
        self.assertEqual(approval.approve(self.kb, self.home, "example", "test", "approve example")[0],
                         "approved")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=0.5):
                    pass
            except OSError:
                break
            time.sleep(0.1)
        else:
            self.fail("approval page still answers after approval")

    def test_start_page_does_not_signal_unverified_ready_pid(self):
        folder, _ = self.reviewed_intent()
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: sleeper.poll() is None and (sleeper.terminate(), sleeper.wait()))

        class Headerless(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Headerless)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        ready = cli.page_ready_path(self.home, "example")
        fsutil.atomic_write_json(ready, {"url": f"http://127.0.0.1:{server.server_port}/", "pid": sleeper.pid})
        with patch("kit.workflow.cli.subprocess.Popen", return_value=object()), patch.object(
            cli, "PAGE_START_SECONDS", 0.3
        ):
            self.assertIsNone(cli._start_page(self.home, folder, "example"))
        self.assertIsNone(sleeper.poll())

    def test_second_submit_replaces_the_first_page(self):
        folder, _ = self.reviewed_intent()
        self.addCleanup(self.cleanup_page, folder)
        ready = cli.page_ready_path(self.home, "example")
        with patch.dict(os.environ, {"KEEL_NO_BROWSER": "1"}):
            self.assertEqual(self.invoke("intent", "submit", "--work", "example")[0], 0)
            first = fsutil.read_json(ready, {})["url"]
            with urllib.request.urlopen(first, timeout=1) as reply:
                self.assertEqual(reply.status, 200)
            result, output = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 0)
        second = fsutil.read_json(ready, {})["url"]
        self.assertNotEqual(first, second)
        self.assertIn(second, output)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(first, timeout=0.5):
                    pass
            except OSError:
                break
            time.sleep(0.1)
        else:
            self.fail("first approval page still answers after the second submit")

    def test_intent_wait_results(self):
        folder, intent = self.reviewed_intent()
        fsutil.atomic_write_text(intent, docs.set_status(COMPLETE_INTENT, "approved"))
        result, _ = self.invoke("intent", "wait", "--work", "example")
        self.assertEqual(result, 0)

        state = work.load_state(folder)
        state["changes_requested"] = {"note": "Clarify the scope"}
        fsutil.atomic_write_json(folder / "state.json", state)
        fsutil.atomic_write_text(intent, COMPLETE_INTENT)
        result, output = self.invoke("intent", "wait", "--work", "example")
        self.assertEqual(result, 1)
        self.assertIn("Clarify the scope", output)

        fsutil.atomic_write_text(intent, docs.set_status(COMPLETE_INTENT, "awaiting-approval"))
        result, _ = self.invoke("intent", "wait", "--work", "example", "--timeout-seconds", "1")
        self.assertEqual(result, 2)

    def test_audit_incomplete_work_prints_problems(self):
        self.invoke("new", "example")
        result, output = self.invoke("audit", "--work", "example")
        self.assertEqual(result, 1)
        self.assertIn("audit-", output)
        self.assertIn("intent not approved", output)

    def _phase_output(self, verdict, fails):
        self.invoke("new", "example")
        folder = work.resolve(self.kb, "example")
        state = work.load_state(folder)
        state["review_fails"] = {"phase-1": fails}
        fsutil.atomic_write_json(folder / "state.json", state)
        result = {"status": "review-failed", "tests_record": "tests.md", "verdict": verdict}
        with patch("kit.workflow.cli.runner.run_phase", return_value=result):
            return self.invoke("run", "phase", "1", "--work", "example")[1]

    def test_fail_prints_next_fix_step(self):
        first = self._phase_output("FAIL", 1)
        self.assertIn("next fix: step 1 of 2", first)
        self.assertIn("sonnet/xhigh (agent keel-hotfix-1)", first)
        second = self._phase_output("FAIL", 2)
        self.assertIn("next fix: step 2 of 2", second)
        self.assertIn("opus/xhigh (agent keel-hotfix-2)", second)

    def test_fail_at_limit_prints_no_further_step(self):
        output = self._phase_output("FAIL", 3)
        self.assertIn("no further fix step", output)
        self.assertNotIn("next fix", output)

    def test_error_prints_rerun_line_only(self):
        output = self._phase_output("ERROR", 0)
        self.assertIn("review errored", output)
        self.assertNotIn("next fix", output)


if __name__ == "__main__":
    unittest.main()
