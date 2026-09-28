---
name: keel-intent
description: Interview for a FULL Keel workflow request, write intent, review intent, and submit it for typed approval. Triggers on FULL work, intent, requirements, or workflow start.
---
<!-- keel -->

# Keel intent

Before the interview, read `{{KB_PATH}}/index.md` and search the KB for the request's key terms. Read each related page by its absolute path. List every KB page read under `## Knowledge base consulted`, including `index.md`; files in the work folder do not count. Interview in the conversation, one open question at a time. Record the user's wording as `Q<n>` in Quotes, and each answer as `D<n>` in Decisions. Restate inferred scope and confirm it with the user. Leave design choices for the spec. Do not submit an intent with open questions.

Run `{{KEEL_WF}} new <slug>` and fill its `intent.md` under `{{KB_PATH}}/raw/work/<work-id>/`. Keep the template's Summary, Problem, Proposed outcome, Affected users and systems, Knowledge base consulted, Constraints, Decisions, Open questions, Quotes, Status, and Changelog sections. Support outcome and constraint claims with `Q<n>` or `D<n>` references.

Run `{{KEEL_WF}} critic intent --work <work-id>`. On FAIL, correct the intent and rerun the review, up to 3 rounds. At the limit, stop and ask the user. On PASS, run `{{KEEL_WF}} intent submit --work <work-id>`. Relay the command's exact approval instruction to the user and end the turn. The user must type the approval; never type it for them or edit `## Status` yourself.
