# Schema

## Folder layout

- `index.md` — the wiki's entry point. Links to pages from the repositories/local-folders/topics
  sections.
- `schema.md` — this file. The folder layout and page-writing rules.
- `raw/sessions/` — unprocessed raw session logs. Never edited by hand.
- `wiki/` — curated topic pages.
- `repos/<name>/` — pages generated from a repository's history (index, timeline, areas).
- `local/<name>/` — index pages generated from a local folder.

## Page rules

- One topic per page.
- Put a `## Last Updated: <date>` line somewhere on the page.
- Link related pages both ways with `[[page name]]` — from this page to the other, and from the
  other back to this one.
- `raw/` holds unprocessed material. Do not edit it by hand until it has been organized into `wiki/`.

## Maintenance

- Now and then (e.g. after a batch of changes, or weekly), ask the agent to run wiki lint.
- A health check (`kb-health`) runs about once a month, after a session ends, and fixes clear
  cases on its own — missing back-links, orphan pages, and broken links with one obvious target.
  Anything ambiguous is left alone and listed in a report at `raw/health/<date>.md`. Say
  "wiki health" to run it now instead of waiting for the monthly run.
- After a substantial session, the wiki may update itself automatically (the `wiki-auto-update`
  hook, which runs the chosen CLI once more and so uses its quota). See the README for what it
  skips and how to turn it off.
