---
name: kb-ingest
description: Add or update a fact in the knowledge base at {{WIKI_PATH}} with two-way [[links]]. Use when the user asks to add, save, update or organize something in the wiki/knowledge base — including "위키 업데이트", "위키에 반영", or "이번 세션 내용"(record the current session).
---
<!-- keel -->

1. If asked to record the current session (e.g. "이번 세션 내용을 위키에 반영해 줘" / 지식
   베이스에 반영해 줘), first summarize the durable facts, decisions and how-tos of this
   conversation — skip anything with no lasting value — then continue with the steps below for
   each one.
2. Search existing pages first (grep `{{WIKI_PATH}}`) for a page that already covers this topic.
3. If a page covers it, update it in place. Otherwise create `{{WIKI_PATH}}/wiki/<topic>.md` with a
   `## Last Updated: <today>` line.
4. Add root-path `[[wiki/<name>]]` links both ways to related pages — from the new/updated page to
   the related ones, and from the related ones back to it.
5. If the page is new, add it to `{{WIKI_PATH}}/index.md` under its topics section — `## Topics` in an English-content KB, `## 주제` in a Korean-content one; use whichever the file actually has.
6. Never put passwords, tokens, or other secrets in the KB.
7. Report which paths changed.
