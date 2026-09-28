from __future__ import annotations

import unittest

from kit.workflow import docs, plan


PLAN = """---
keel_plan: 1
---
# Plan: Example

## Phase 1: Build
> Scenarios: S1, S2

### Tests
- Run: `python3 -m unittest tests.test_one`

- [ ] First step
- [x] Second step

## Phase 2: Verify
> Scenarios: S3

### Tests
- Run: `npm test`

- [ ] Third step
"""


class PlanTest(unittest.TestCase):
    def test_parse_two_phases(self):
        phases = plan.parse(PLAN)
        self.assertEqual([(phase.n, phase.title) for phase in phases], [(1, "Build"), (2, "Verify")])
        self.assertEqual([phase.scenarios for phase in phases], [["S1", "S2"], ["S3"]])
        self.assertEqual([phase.tests for phase in phases],
                         [["python3 -m unittest tests.test_one"], ["npm test"]])
        self.assertEqual([len(phase.steps) for phase in phases], [2, 1])
        self.assertEqual([ticked for _, ticked in phases[0].steps], [False, True])

    def test_missing_front_matter(self):
        with self.assertRaises(plan.PlanError):
            plan.parse(PLAN.split("---\n", 2)[-1])

    def test_run_requires_backticks(self):
        with self.assertRaises(plan.PlanError):
            plan.parse(PLAN.replace("- Run: `npm test`", "- Run: npm test"))

    def test_rejects_misspelled_tests_heading(self):
        for heading in ("### Test", "### Testing", "### tests"):
            with self.subTest(heading=heading), self.assertRaises(plan.PlanError):
                plan.parse(PLAN.replace("### Tests", heading, 1))

    def test_final_phase_stops_at_next_non_phase_heading(self):
        text = PLAN + "\n## Appendix\n- [ ] Not a phase step\n"
        phases = plan.parse(text)
        self.assertEqual(len(phases[-1].steps), 1)
        self.assertEqual(phases[-1].end, text.splitlines().index("## Appendix"))

    def test_non_final_phase_keeps_steps_after_notes_heading(self):
        text = PLAN.replace("- [x] Second step", "## Notes\n- [ ] Note step\n- [x] Second step")
        phases = plan.parse(text)
        self.assertEqual(len(phases[0].steps), 3)
        self.assertEqual(phases[0].end, text.splitlines().index("## Phase 2: Verify"))

    def test_fenced_heading_and_step_do_not_affect_final_phase(self):
        text = PLAN + "\n```text\n## Summary\n- [ ] Fenced step\n```\n- [ ] Last step\n"
        phases = plan.parse(text)
        self.assertEqual(len(phases[-1].steps), 2)
        self.assertEqual(phases[-1].end, len(text.splitlines()))
        ticked = plan.tick_phase(text, 2)
        self.assertIn("- [ ] Fenced step", ticked)
        self.assertIn("- [x] Last step", ticked)

    def test_outward_steps_ignores_quotes(self):
        self.assertTrue(plan.outward_steps("- [ ] git push origin main\n"))
        self.assertEqual(plan.outward_steps("> - [ ] git push origin main\n"), [])

    def test_outward_steps_refuses_branch_changes(self):
        for line in ("git switch -c feature/x", "git switch main", "git checkout -b x", "git checkout -B x",
                     "git checkout --orphan x", "git checkout main", "git branch topic", "git branch -c a b",
                     "git worktree add ../w", "gh pr checkout 12", "git commit -m x"):
            with self.subTest(line=line):
                self.assertTrue(plan.outward_steps(f"- [x] Run `{line}`\n"))

    def test_outward_steps_allows_read_only_git(self):
        for line in ("git status", "git diff", "git log --oneline", "git branch", "git branch --show-current",
                     "git branch -a", "git checkout -- README.md", "git checkout HEAD -- README.md"):
            with self.subTest(line=line):
                self.assertEqual(plan.outward_steps(f"- [x] Run `{line}`\n"), [])

    def test_tick_phase_preserves_hash(self):
        ticked = plan.tick_phase(PLAN, 1)
        phases = plan.parse(ticked)
        self.assertEqual([ticked for _, ticked in phases[0].steps], [True, True])
        self.assertEqual([ticked for _, ticked in phases[1].steps], [False])
        self.assertEqual(docs.plan_hash(PLAN), docs.plan_hash(ticked))


if __name__ == "__main__":
    unittest.main()
