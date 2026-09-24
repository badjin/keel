---
name: kb-health
description: Health-check and auto-fix the knowledge base at {{WIKI_PATH}} — orphan pages, broken
  links, ambiguous links, and one-way links, fixing what is safe and reporting the rest.
  Use for kb health, wiki health, knowledge base health, 위키 헬스, 지식 베이스 헬스, 지식 베이스 건강.
---
<!-- keel -->

1. List every `.md` file under `{{WIKI_PATH}}` (skip `.git`, `.obsidian`, and `raw/`).
2. Resolve each `[[target]]` or `[[target|label]]` the Obsidian way — skip any `[[` inside a code
   span/block or escaped as `\[`. An empty target (e.g. `[[#heading]]`) means the current page.
   Strip a trailing `#heading`, `^block`, or `.md` from `target`, adding `.md` back when checking
   it as a path: try it first as a path from the KB root. If that path does not exist, fall
   back to matching it by file name anywhere under `{{WIKI_PATH}}`. Compare names after NFC
   normalization and ignoring case in both the path step and the file-name fallback.
3. Find the four problem lists (same as `kb-lint`): broken links (neither the path step nor the
   file-name fallback resolves it), ambiguous links (the file-name fallback matches more than one
   file), orphans (pages under `wiki/`, `repos/`, or `local/` that no other page links to and that
   the root `index.md` does not list), and one-way links (a page under `wiki/` links to another
   page under `wiki/` that has no link back).
4. Fix without asking, unlike `kb-lint` which reports first and asks:
   - add the missing back-link for each one-way link,
   - link each orphan from the root `index.md` or the most related `wiki/` page,
   - for each broken link, point it at the existing page it clearly meant.
   Write every new link as a path from the KB root (e.g. `[[wiki/a]]`).
   Scope rule for every fix above, with no exception: only ever edit `index.md` or pages under
   `wiki/`. Never edit `local/` or `repos/` — a page under either is regenerated on every sync (a
   `repos/` page starts with `<!-- repo:`) and any link added there is discarded.
5. Leave everything ambiguous exactly as it is: several file-name candidates, no clear target for
   a broken link, or a link to a page not yet written (often deliberate). Do not touch the
   `[[...]]` brackets, and do not ask the user — this skill also runs automatically about once a
   month, after a session ends, with nobody there to answer. List each ambiguous item in the
   report instead.
6. Write the report to `{{WIKI_PATH}}/raw/health/<YYYY-MM-DD>.md` (today's date) — the only file
   this run may write anywhere under `raw/`. Include the four problem lists and which items were
   fixed vs. left ambiguous.
7. Never delete a page or a file, anywhere.
8. Report which files changed.
