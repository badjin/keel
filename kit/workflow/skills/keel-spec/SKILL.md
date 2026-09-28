---
name: keel-spec
description: Write and review a Keel workflow specification after intent approval. Triggers on spec, design, acceptance scenarios, or approved intent.
---
<!-- keel -->

# Keel spec

Read the approved intent in `{{KB_PATH}}/raw/work/<work-id>/intent.md`. Write `spec.md` in that work folder using the template sections: Operational Context, Design, Acceptance Scenarios, and Out of scope. Preserve the `- Intent:` and `- Scenario review:` header lines. Ground the design in the approved intent and confirmed repository facts. Put no branch steps in the spec; the runner creates or keeps the work branch at `run start`.

Write each numbered scenario as **where / do what / expect what**. Use `Exempt: <reason>` only when the work has no applicable scenarios. Run `{{KEEL_WF}} critic spec --work <work-id>` and resolve its feedback before proceeding. The approved scenario list is the verification contract. If a scenario changes, edit the spec first and run `critic spec` again.
