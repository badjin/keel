from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kit.workflow import cli, docs, fsutil, work


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
        self.invoke("new", "example")
        folder = work.resolve(self.kb, "example")
        intent = folder / "intent.md"
        fsutil.atomic_write_text(intent, COMPLETE_INTENT)
        state = work.load_state(folder)
        state["reviews"]["intent"] = {
            "verdict": "PASS", "hashes": {"intent": docs.intent_hash(COMPLETE_INTENT)},
        }
        fsutil.atomic_write_json(folder / "state.json", state)

        result, output = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 0)
        self.assertEqual(docs.get_status(intent.read_text()), "awaiting-approval")
        self.assertIn("approve example", output)
        self.assertIn(str(intent.resolve()), output)

        fsutil.atomic_write_text(intent, COMPLETE_INTENT.replace("missing", "incomplete"))
        result, _ = self.invoke("intent", "submit", "--work", "example")
        self.assertEqual(result, 1)

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
