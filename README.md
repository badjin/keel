# Keel

Knowledge base and hooks for AI coding agents.

[한국어](README.ko.md)

Built on Andrej Karpathy's LLM Wiki idea (https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

Installs a personal Knowledge Base (KB) for use with Claude Code / Codex as a local web app.
Hook install, KB creation, GitHub/local folder history indexing, and
Obsidian connection are all chosen by the user directly, in the browser
screen.

- A **hook** is a small script that automatically steps in at a fixed
  moment while Claude Code / Codex is working (session start, answer
  finished, etc.). Example: having the KB automatically loaded when a
  session starts.
- **CLI** means a program used from the terminal (the black screen where you
  type commands). Here it refers to three: `claude`, `codex`, and `gh`.

## Get Keel

Pick one of these three. All of them end with the same install page in your browser.

1. **Download (easiest).** Open the [latest release](https://github.com/badjin/keel/releases/latest)
   and download `keel-<version>.zip` and `INSTALL.md`. Put both files in one
   new folder, open Terminal in that folder, run `claude` (or `codex`) and say:
   `Follow INSTALL.md`.
2. **Clone.**
   ```
   git clone https://github.com/badjin/keel
   cd keel
   claude
   ```
   Then say: `Follow INSTALL.md`.
3. **Ask your AI agent.** In a new, empty folder, open `claude` or `codex` and say:
   `Clone https://github.com/badjin/keel and follow its INSTALL.md`.

## Requirements

- macOS
- `python3` 3.9 or newer (standard library only, nothing else to install)
- `git`
- (optional) the `claude`, `codex` CLIs, the `gh` CLI, Obsidian — everything
  else still works without them

If this is a Mac you're opening for the first time, an `xcode-select
--install` window may pop up suddenly during the steps below. That's the
normal procedure for installing Apple's developer tools — if it appears,
just click Install, and try again once it finishes.

## Quick start

**If you have an AI agent (Claude Code or Codex), start in this order:**

1. **Open the Terminal app.** (Spotlight search — the magnifying glass at
   the top right of the screen — type `Terminal` and press Enter)
2. **Move into the kit folder, then open `claude` or `codex` interactively.**
   (Type `cd ` and then drag the unzipped kit folder from Finder into the
   Terminal window and press Enter. This uses an interactive session
   instead of a one-shot `claude -p` / `codex exec` — Codex will ask
   whether to trust the folder the first time it's opened; allow it then.)
3. Tell the agent **"read `INSTALL.md` and follow it."** If it asks
   whether it may run the command that starts the server, allow it. The
   agent only starts the server — once the `Open / 열기:` address printed
   afterward in the terminal opens in the browser, the user follows the four
   steps in the browser page directly (Hooks → Knowledge Base → GitHub → Finish). To
   stop the server the agent started, either ask the agent to stop it, or
   close that terminal window.

### Starting it yourself, without AI

1. **Open the Terminal app.** (Spotlight search — the magnifying glass at
   the top right of the screen — type `Terminal` and press Enter)
2. **Move into the kit folder.** In the terminal, type just `cd ` (cd
   followed by one space), then drag the unzipped kit folder from Finder
   into the Terminal window — the path fills in automatically. Then press
   Enter.
3. **Start the server.**

```
python3 app/server.py
```

Opening the `Open / 열기:` address printed in the terminal in your browser starts
the install screen.

4. **To stop the server** once you're done, go back to the terminal window
   where you started it and press `Control` and `C` together (Ctrl+C).
   Closing the window also works.

## The four tabs

1. **Hooks** — choose which hooks to plant in Claude Code / Codex
   (auto-loading the KB, blocking speculative answers, requiring
   verification before done, etc.). Only installed CLIs are shown.
2. **Knowledge Base** — decide where to build the KB. Start with an empty KB, or
   pick local folders to build an index from.
3. **GitHub (optional)** — turns commit history from a GitHub repository or
   a local git repository, over a chosen period, into KB pages. Add an
   LLM summary too if you want.
4. **Finish / Obsidian** — shows an install summary, and opens the KB
   directly if Obsidian is present. If not, shows install guidance.

### Automatic handoff

The optional **Automatic handoff** hook is off by default. In the Hooks tab,
choose Claude Code, Codex, or Grok for this hook and set its context threshold
(40–80%, initially 60%). Grok auto-compacts at 85%, so the handoff threshold
stops at 80%. Enabling it installs the `handoff` skill for the selected CLIs.
At the threshold, the skill saves the current task under
`<cwd>/.keel/handoff/`. In Herdr or tmux, the session resets and resumes in
the same pane; elsewhere, type `/clear` in Claude Code or `/new` in Codex or
Grok. Removing the hook restores any existing status line command.
Outside Herdr and tmux, Grok needs a follow-up request to restore after `/new`:
its SessionStart hook output is not delivered to the agent.

## Using Keel? Tell us

Using Keel? Star <a href="https://github.com/badjin/keel" target="_blank" rel="noopener">https://github.com/badjin/keel</a> or say hi in its Discussions (<a href="https://github.com/badjin/keel/discussions" target="_blank" rel="noopener">https://github.com/badjin/keel/discussions</a>) — it tells us people use it.

## Privacy

- The GitHub token exists only in memory while the server is running. It is
  never stored on disk, in a log, in `.git/config`, or in a URL.
- Only commit metadata (date, author, subject/title, touched file paths) is
  read and recorded. File contents are never read.
- If you turn on the optional LLM summary feature, commit titles are sent to
  whichever CLI (`claude` or `codex`) you chose, to its provider.

## KB maintenance — run the lint every so often

As you use the KB, orphan pages (pages nothing links to), broken links
(a `[[link]]` pointing to a page that doesn't exist), and one-way links (A
links to B but B doesn't link back to A) build up. These problems do not fix
themselves.

Every so often (after a batch of changes, or roughly weekly), from any
folder tell Claude Code or Codex "run the KB lint" (or "run the wiki lint").
The `kb-lint` skill finds and fixes the three kinds of problem. Turning on
**"Also remove skills"** when removing skills on the Hooks tab also removes
`kb-lint`.

## Undo order

**The order matters.** If you delete the `~/.keel` folder before
removing the hooks, the scripts the already-registered hooks point to
disappear, and Claude Code / Codex can error out and get stuck. Follow the
order below.

1. **Remove the hooks first.** Start the server again
   (`python3 app/server.py`), leave every visible CLI checkbox (Claude
   Code, Codex, etc.) turned on, then press the **"Remove all kit hooks"**
   button on the Hooks tab. Turning a checkbox off excludes that CLI from
   removal and can leave its hooks in place. To also remove the KB
   skills you installed, turn on the **"Also remove skills"** checkbox.
   (If you can't use the button, `.bak-before-kit` next to
   `~/.claude/settings.json` / `~/.codex/hooks.json` is written by the kit
   only when that file did not exist and no sentinel existed yet at
   install time — restoring it gets you out of the lockout, but any change
   made to that file afterward disappears with it. If it doesn't exist,
   restore the newest `*.bak-*` backup in the same place for which
   `grep -c '/.keel/hooks/' <backup file>` prints `0` — that value
   being `0` means the backup predates the kit install. Restoring a backup
   that isn't `0` restores a state that already has kit hooks in it, and
   then deleting `~/.keel` reproduces the error above.
   `~/.codex/config.toml` needs no restoring — it carries no kit hook
   command and cannot cause the lockout, and `hooks = true` is covered in
   the paragraph below. A config file with neither a `.bak-before-kit` nor
   any `*.bak-*` was created new by the kit — there is no original to
   restore, so use the button instead.)
2. **Only then** delete everything else the kit created:

```
rm -rf ~/.keel
```

This command does not delete the KB folder itself or Obsidian.

The `hooks = true` line in `~/.codex/config.toml` is not removed by this
procedure — other, non-kit Codex hooks may depend on that setting. Remove
it yourself only if you are certain it isn't needed.

## KB auto-update

The `wiki-auto-update` hook (on by default) decides what to do each time a
session ends.

- **When it skips** — if the session was short (fewer than 2 real user
  messages, or under 200 characters total) or had no content, it does
  nothing. It also skips if the CLI used in this session (`claude`/`codex`)
  is not on PATH.
- **When it applies automatically** — if the folder just worked in is a git
  repository, or the conversation content matches a topic already in the
  KB root's `index.md`, it updates the KB automatically.
- **Direct request** — an explicit request like "update the wiki" / update
  the KB, or "add this session to the wiki" / add this session to the KB,
  is handled on the spot by the `kb-ingest` skill (separate from this
  hook).
- **Uses quota** — the update runs the chosen CLI once more in the
  background after the session ends, so it uses that CLI's usage quota.
- **Temporary files** — this session's conversation content is briefly
  saved as a working file under `~/.keel/state/auto-update/`,
  handed to the chosen CLI, and deleted right after processing finishes
  (whether it was applied, skipped, or errored). It can only remain behind
  if the background worker process itself is killed mid-run.
- **Read/write scope of the background run** — Codex's background run can
  read files outside the KB (its writes stay limited to the KB, via
  its own sandboxed working directory); Claude's background run is
  limited to the KB for both reading and writing.
- **How to turn it off** — turn off the "Auto-update the knowledge base when
  a session ends" checkbox on the Hooks tab and reinstall.
- **Log** — `~/.keel/state/auto-update.log` shows why sessions were
  skipped and what was done.

## Limitations

- macOS only (the path conventions assume macOS). Windows/Linux are not
  supported.
