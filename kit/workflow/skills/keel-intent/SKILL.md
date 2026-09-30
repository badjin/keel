---
name: keel-intent
description: Interview for a FULL Keel workflow request, write intent, review intent, and submit it for approval (approval page or typed). Triggers on FULL work, intent, requirements, or workflow start.
---
<!-- keel -->

# Keel intent

Before the interview, read `{{KB_PATH}}/index.md` and search the KB for the request's key terms. Read each related page by its absolute path. List every KB page read under `## Knowledge base consulted`, including `index.md`; files in the work folder do not count. Interview in the conversation, one open question at a time. Record the user's wording as `Q<n>` in Quotes, and each answer as `D<n>` in Decisions. Restate inferred scope and confirm it with the user. Leave design choices for the spec. Do not submit an intent with open questions.

Run `{{KEEL_WF}} new <slug>` and fill its `intent.md` under `{{KB_PATH}}/raw/work/<work-id>/`. Keep the template's Summary, Problem, Proposed outcome, Affected users and systems, Knowledge base consulted, Constraints, Decisions, Open questions, Quotes, Status, and Changelog sections. Support outcome and constraint claims with `Q<n>` or `D<n>` references.

Run `{{KEEL_WF}} critic intent --work <work-id>`. On FAIL, correct the intent and rerun the review, up to 3 rounds. At the limit, stop and ask the user. On PASS, run `{{KEEL_WF}} intent submit --work <work-id>`. Relay the command's exact approval instruction to the user. If submit prints a `review --work <work-id>` command instead of a page address, relay it to the user; typed approval still works. If your tool can run a command in the background and resume when it ends (Claude Code: Bash with run_in_background), start `{{KEEL_WF}} intent wait --work <work-id>` that way with a background timeout longer than its --timeout-seconds (default 1800), then end the turn; when it ends, follow its printed instruction. Otherwise end the turn and wait for the user's next message. The user must approve on the page or type the approval; never click or type it for them, call the approval page's address yourself, or edit `## Status` yourself.
