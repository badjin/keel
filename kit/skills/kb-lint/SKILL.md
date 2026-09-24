---
name: kb-lint
description: Health-check the knowledge base at {{WIKI_PATH}} for orphan pages and broken links.
  Use for a wiki lint, kb lint, 위키 린트, 위키 점검, 지식 베이스 린트, 지식 베이스 점검, an orphan, or a broken link.
---
<!-- keel -->

1. List every `.md` file under `{{WIKI_PATH}}` (skip `.git`, `.obsidian`, and `raw/`).
2. Resolve each `[[target]]` or `[[target|label]]` the Obsidian way — skip any `[[` inside a code
   span/block or escaped as `\[`. An empty target (e.g. `[[#heading]]`) means the current page.
   Strip a trailing `#heading`, `^block`, or `.md` from `target`, adding `.md` back when checking
   it as a path: try it first as a path from the KB root. If that path does not exist, fall
   back to matching it by file name anywhere under `{{WIKI_PATH}}`. Compare names after NFC
   normalization and ignoring case in both the path step and the file-name fallback.
3. If the file-name fallback matches more than one file, report it as ambiguous, leave the link
   as it is, and ask the user which page was meant; do not count it toward the orphan or one-way
   links below. Flag a link broken only if neither the path step nor the fallback resolves it.
4. Orphans: pages under `wiki/`, `repos/`, or `local/` that no other page links to and that
   the root `index.md` does not list.
5. One-way links: a page under `wiki/` links to another page under `wiki/` that has no link back.
6. Report the four lists (broken links, ambiguous links, orphans, one-way links) first, then fix
   what is safe. Write every new link as a path from the KB root (e.g. `[[wiki/a]]`):
   - add the missing back-link for each one-way link,
   - link each orphan from the root `index.md` or the most related `wiki/` page,
   - for each broken link, point it at the existing page it clearly meant.
   Scope rule for every fix above, with no exception: only ever edit `index.md` or pages under
   `wiki/`. Never edit `local/` or `repos/` — a page under either is regenerated on every sync (a
   `repos/` page starts with `<!-- repo:`) and any link added there is discarded.
   If a broken link has no clear target, leave the `[[...]]` brackets alone — list it and ask the
   user, since a link to a page not yet written is often deliberate.
   Never delete a page or a file under `raw/`.
7. Report which files changed.
