---
name: keel-run
description: Run reviewed Keel phases, verify approved scenarios, audit, and archive. Triggers on run, implementation, phase, verification, audit, or archive.
---
<!-- keel -->

# Keel run

Read the reviewed plan in `{{KB_PATH}}/raw/work/<work-id>/plan.md`. Run `{{KEEL_WF}} run start --work <work-id> --repo <repo>`. Do not create or switch branches yourself: `run start` creates `keel/<slug>` when the repository is on `main` or `master`, and otherwise runs on the current branch. For each phase, implement its steps and then run `{{KEEL_WF}} run phase N --work <work-id>`. The runner runs the phase's tests, commits scoped changes, and reviews the phase. On a phase review FAIL, do not fix it yourself. Read the printed `next fix` line and follow it:

- Claude Code: delegate the fix to the named agent (`keel-hotfix-<step>`, the step number printed on the `next fix` line, not the phase number `N`), passing the review record path and the phase's plan section, then re-run `{{KEEL_WF}} run phase N --work <work-id>` yourself.
- Codex: run the fix with `codex exec -s workspace-write -c model_reasoning_effort=<effort>`, adding `-m <model>` only when a model is printed. A nested `codex exec` does not see this skill, so write the fixer rules into the prompt you give it: fix only what the review findings name, do not commit, do not run `{{KEEL_WF}}`, do not create or switch branches. The prompt also carries the review record path and the phase's plan section. After the fix, re-run `{{KEEL_WF}} run phase N --work <work-id>` yourself. If a nested `codex exec` cannot be started from this session, stop and tell the user which model and effort to switch to.
- `review errored`: re-run `run phase N` with no fix.
- `no further fix step`: stop and ask the user.

The phase review limit is set in `~/.keel/ladder.json` (default 3).

After all phases, run every scenario approved by the spec review in its stated environment. Write a results JSON file under the work folder with one entry per scenario, for example `{"S1": {"result": "PASS", "evidence": "what was observed"}}`. Run `{{KEEL_WF}} verify --work <work-id> --results <results.json>`; for an approved exemption, use `--exempt`. Then run `{{KEEL_WF}} audit --work <work-id>` and `{{KEEL_WF}} archive --work <work-id>`. Report the verified outcome and any limits. Do not push, merge, or deploy as part of these stages.
