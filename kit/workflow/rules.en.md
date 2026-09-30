If your prompt starts with `KEEL-CRITIC`, ignore this whole block.

# Keel workflow

For substantive work, make your first line `Grade: FULL — <reason>` or `Grade: LIGHT — <reason>`.
FULL means end users or the production runtime consume the change: runtime code, schema,
deploy or CI configuration, dependency versions, runtime configuration, or user-facing text.
Even a one-line change in those areas is FULL.
LIGHT means only developers consume the change: developer documentation, local tools, or notes.
If the grade is unclear, choose FULL and ask. Pure questions need no grade.

Run Keel commands with `{{KEEL_WF}}`.
Keep work documents only in `{{KB_PATH}}/raw/work/<work-id>/`.
Use the installed skills for the stage you are performing.

## FULL stages

1. Use keel-intent. Before the interview, read `{{KB_PATH}}/index.md`, search the KB for the request's key terms, and read each related page by its absolute path. List every page read under `## Knowledge base consulted`; `index.md` counts, but files in the work folder do not. Then interview the user before choosing a design.
   Run `{{KEEL_WF}} new <slug>` and fill the generated intent.md.
   Run `{{KEEL_WF}} critic intent --work <work-id>`; fix FAIL and rerun, at most 3 rounds.
   Run `{{KEEL_WF}} intent submit --work <work-id>`.
   Relay its instruction and end the turn.
2. Wait for the user to type `approve <slug>`.
   The prompt hook records approval. Never type that message for the user.
3. Use keel-spec. Write spec.md from the approved intent, including acceptance scenarios.
   Run `{{KEEL_WF}} critic spec --work <work-id>` and resolve its feedback.
4. Use keel-plan. Write plan.md with phases, scenario coverage, and test commands.
   Run `{{KEEL_WF}} critic plan --work <work-id>` and resolve its feedback.
5. Use keel-run. Run `{{KEEL_WF}} run start --work <work-id> --repo <repo>`.
   Implement each phase, then run `{{KEEL_WF}} run phase N --work <work-id>`.
   On a failed phase review do not fix it yourself: read the printed `next fix` line and follow keel-run.
   The phase review limit is set in `~/.keel/ladder.json` (default 3).
6. Run every approved acceptance scenario in its stated environment.
   Record each result and evidence in a JSON file.
   Run `{{KEEL_WF}} verify --work <work-id> --results <results.json>`.
   For an approved exemption, use `--exempt` instead of `--results`.
7. Run `{{KEEL_WF}} audit --work <work-id>`, then
   `{{KEEL_WF}} archive --work <work-id>` and report the outcome.

## Hard rules

- Never type, impersonate, or fake the user's approval. The user must supply it.
- Never edit `## Status` by hand; the Keel commands and approval hook own that field.
- Never push, merge, or deploy as part of a workflow stage.
- Never create or switch branches yourself. `run start` creates or keeps the work branch; spec and plan hold no branch steps.
- If intent review reaches 3 rounds, or the runner prints `no further fix step`, stop and ask the user.
- Do not continue from a failed or stale review. Correct the document or implementation and rerun it.
- A changed acceptance scenario requires a new spec review before plan or verification.
- Keep intent, spec, plan, records, and scenario results under `{{KB_PATH}}/raw/work/`.

## LIGHT path

Make the developer-only change, then run
`{{KEEL_WF}} critic light --repo <repo> --request "<one-line request>"`.
Resolve review feedback before reporting completion.
