from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kit.workflow import docs, fsutil


INTENT = """# Intent: Example
## Summary
Example.
## Problem
The old path fails.
## Proposed outcome
- Fix the path. [Q1]
## Affected users and systems
Users.
## Knowledge base consulted
- `index.md` — Workflow overview.
## Constraints
- Keep the old data. [D1]
## Decisions
**D1** Keep existing data.
## Open questions
None.
## Quotes
**Q1** Fix the path.
## Status
`draft`
## Changelog
- Created.
"""


class DocsTest(unittest.TestCase):
    def test_intent_hash_ignores_status_and_changelog(self):
        changed = INTENT.replace("`draft`", "`approved`").replace("- Created.", "- Approved.")
        self.assertEqual(docs.intent_hash(INTENT), docs.intent_hash(changed))
        self.assertNotEqual(docs.intent_hash(INTENT), docs.intent_hash(INTENT.replace("old path", "new path")))

    def test_intent_hash_ignores_crlf_and_trailing_spaces(self):
        self.assertEqual(docs.intent_hash(INTENT),
                         docs.intent_hash(INTENT.replace("\n", "  \r\n")))

    def test_plan_hash_ignores_checkboxes(self):
        plan = "## Phase 1\n  - [ ] First\n- [ ] Second\n"
        self.assertEqual(docs.plan_hash(plan), docs.plan_hash(plan.replace("- [ ]", "- [x]")))

    def test_spec_hash_ignores_review_pointer(self):
        spec = "# Spec\n- Scenario review: pending\n## Acceptance Scenarios\n- S1 Do this.\n"
        self.assertEqual(docs.spec_hash(spec), docs.spec_hash(spec.replace("pending", "review.json")))
        self.assertNotEqual(docs.spec_hash(spec), docs.spec_hash(spec.replace("Do this", "Do that")))

    def test_check_intent(self):
        self.assertEqual(docs.check_intent(INTENT, ["None.", "None"]), [])
        self.assertIn("missing section: Quotes", docs.check_intent(INTENT.replace("## Quotes\n**Q1** Fix the path.\n", ""), ["None."]))
        self.assertIn("open questions remain", docs.check_intent(INTENT.replace("## Open questions\nNone.", "## Open questions\nWhich DB?"), ["None."]))
        self.assertEqual(docs.check_intent(INTENT.replace("None.", ""), ["None.", "None"]), [])
        self.assertIn("bullet without [Q#]/[D#]: - Fix the path.", docs.check_intent(INTENT.replace("- Fix the path. [Q1]", "- Fix the path."), ["None."]))
        self.assertIn("unknown reference: Q9", docs.check_intent(INTENT.replace("[Q1]", "[Q9]"), ["None."]))

    def test_kb_pages_parses_paths_links_labels_and_headings(self):
        intent = INTENT.replace(
            "- `index.md` — Workflow overview.",
            "- `index.md` — Workflow overview.\n"
            "- [[wiki/a]] — Context.\n"
            "- [[wiki/b#details|Read this]] — More context.",
        )
        self.assertEqual(docs.kb_pages(intent), ["index.md", "wiki/a.md", "wiki/b.md"])

    def test_check_intent_requires_parseable_kb_bullet(self):
        missing = INTENT.replace("## Knowledge base consulted\n- `index.md` — Workflow overview.\n", "")
        empty = INTENT.replace("- `index.md` — Workflow overview.", "")
        unparseable = INTENT.replace("- `index.md` — Workflow overview.", "- index.md — Workflow overview.")
        self.assertTrue(docs.check_intent(missing, ["None."]))
        self.assertTrue(docs.check_intent(empty, ["None."]))
        self.assertTrue(docs.check_intent(unparseable, ["None."]))

    def test_spec_scenarios_and_fenced_heading(self):
        spec = """## Acceptance Scenarios
- **S1** Open a page.
  Continue the description.
- S2 Save it.
```md
## Hidden
```
"""
        self.assertEqual(docs.spec_scenarios(spec), {"scenarios": ["**S1** Open a page. Continue the description.", "S2 Save it."]})
        self.assertNotIn("Hidden", docs.sections(spec))
        self.assertEqual(docs.spec_scenarios("## Acceptance Scenarios\nExempt: docs only\n"), {"exempt": "docs only"})

    def test_note_continuation_does_not_extend_scenario(self):
        spec = "## Acceptance Scenarios\n- S1 Open page.\n- Note: details\n  Not a scenario continuation.\n"
        self.assertEqual(docs.spec_scenarios(spec), {"scenarios": ["S1 Open page."]})

    def test_scenario_bullet_forms_and_comments(self):
        spec = "## Acceptance Scenarios\n- **S1:** a\n- **S1.** b\n- **S1 — x** c\n- S2 d\n"
        self.assertEqual(len(docs.spec_scenarios(spec)["scenarios"]), 4)
        self.assertEqual(docs.spec_scenarios("## Acceptance Scenarios\n<!-- guidance\nmore -->\nExempt: docs\n"),
                         {"exempt": "docs"})

    def test_template_comments_do_not_fill_sections_or_reject_quotes(self):
        template = (Path(__file__).resolve().parents[1] / "kit/workflow/templates/intent.en.md").read_text()
        self.assertIn("empty section: Summary", docs.check_intent(template, ["None."]))
        quoted = INTENT.replace("**Q1** Fix the path.", "**Q1** Fix <!-- x --> the path.")
        self.assertEqual(docs.check_intent(quoted, ["None."]), [])

    def test_update_json_rereads_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            fsutil.update_json(path, lambda data: data.update(first=1))
            fsutil.update_json(path, lambda data: data.update(second=2))
            self.assertEqual(fsutil.read_json(path, {}), {"first": 1, "second": 2})


if __name__ == "__main__":
    unittest.main()
