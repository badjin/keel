---
name: keel-run
description: Run reviewed Keel phases, verify approved scenarios, audit, and archive. Triggers on run, implementation, phase, verification, audit, or archive.
---
<!-- keel -->

# Keel run

Read the reviewed plan in `{{KB_PATH}}/raw/work/<work-id>/plan.md`. Run `{{KEEL_WF}} run start --work <work-id> --repo <repo>`. Do not create or switch branches yourself: `run start` creates `keel/<slug>` when the repository is on `main` or `master`, and otherwise runs on the current branch. For each phase, implement its steps and then run `{{KEEL_WF}} run phase N --work <work-id>`. The runner runs the phase's tests, commits scoped changes, and reviews the phase. On FAIL, correct the implementation and retry; at the 2-round phase review limit, stop and ask the user.

After all phases, run every scenario approved by the spec review in its stated environment. Write a results JSON file under the work folder with one entry per scenario, for example `{"S1": {"result": "PASS", "evidence": "what was observed"}}`. Run `{{KEEL_WF}} verify --work <work-id> --results <results.json>`; for an approved exemption, use `--exempt`. Then run `{{KEEL_WF}} audit --work <work-id>` and `{{KEEL_WF}} archive --work <work-id>`. Report the verified outcome and any limits. Do not push, merge, or deploy as part of these stages.
