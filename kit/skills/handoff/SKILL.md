---
name: handoff
description: Save and restore work across a context reset, including the automatic handoff requested by a Keel Stop hook.
---
<!-- keel -->

# Handoff

## Auto action

When the Keel Stop hook asks for an automatic handoff, use the session ID, CLI, and cwd in its reason. Do not ask for confirmation.

1. Create `<cwd>/.keel/` with a `.gitignore` containing `*` if it does not exist, then create `<cwd>/.keel/handoff/handoff-<YYYY-MM-DD-HH-MM-SS>.md`. Record the user's objective, constraints, completed work, modified files, current branch, unfinished work, blockers, and the exact next action. Include enough detail for a new session to continue without guessing. Do not include secrets.
2. Run `python3 ~/.keel/hooks/handoff_auto.py --handoff <absolute handoff path> --sid <session ID> --target <claude|codex|grok> --cwd <absolute cwd>`. It writes the marker and starts the detached reset driver inside Herdr or tmux.
3. End this turn. Outside Herdr and tmux, relay the command's reset instructions to the user in the installed UI language.

## Restore action

When SessionStart reports a handoff, read the exact file it names. Move that file to `<cwd>/.keel/handoff/active/`, preserving its name. Delete the named marker immediately after moving the file. Continue the unfinished work without asking the user to approve the restore. Do not take an unrelated active handoff from another session.
