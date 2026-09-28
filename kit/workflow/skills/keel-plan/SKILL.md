---
name: keel-plan
description: Write and review a phased Keel workflow implementation plan. Triggers on plan, phases, test commands, or spec approval.
---
<!-- keel -->

# Keel plan

Read the reviewed spec in `{{KB_PATH}}/raw/work/<work-id>/spec.md`. Write `plan.md` beside it. Start with the exact YAML front matter `---`, `keel_plan: 1`, `---`. Use `## Phase N: <title>` in order, `> Scenarios: S1, S2` for each phase's coverage, `### Tests`, and ``- Run: `<command>` `` for executable test commands. Use `- [ ]` steps for the work.

Budget tests for behavior whose failure causes real harm: file writes or rollback, installation, secrets, data paths or schemas, money, sending, permissions, or core calculations and parsing. Do not add tests that compare human-facing wording. Keep push, merge, deploy, and other outward actions outside the runner phases.

Run `{{KEEL_WF}} critic plan --work <work-id>`. Correct FAIL feedback and rerun before starting implementation.
