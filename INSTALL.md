# INSTALL — Keel

Give this file to Claude Code or Codex and say: follow this file. It walks
the agent through unzipping the kit, starting the local server, and handing
the rest of the install over to you in the browser.

You (the agent reading this file) are running on a fresh machine with no other
context about this task. This file is self-sufficient — follow it exactly,
in order. It sets up a Knowledge Base (KB) and optional Claude Code / Codex hooks.
The Hooks tab also offers optional automatic handoff for Claude Code, Codex,
and Grok. It is off by default; the human chooses its CLIs and threshold.
**Every choice — which hooks, where the KB goes, which repos to index — is
made by the human user inside a local web page. You do not drive a browser
and you do not pick any option for the user.** Your job is: unzip the kit,
check prerequisites, start the local server, hand the user the URL, wait,
then verify and report — answering the user in the user's own language.

## 0. Find and unzip the kit

If you are already running from inside the unzipped kit folder (this file
and `app/server.py` are both here), skip this step and go to step 1.

Otherwise, first `cd` to the folder that contains this INSTALL.md file, then
look there for `keel-*.zip`:

- If exactly one `keel-*.zip` is present, unzip it without
  overwriting anything already there and `cd` into the result:

```
unzip -n -q keel-*.zip
cd keel-*/
```

- If more than one `keel-*.zip` is present, pick the newest one
  by modification time, tell the user (in their own language) which zip you
  picked, and unzip only that one the same way.
- If there is no zip but an already-unzipped `keel*/` folder is
  present that already contains both `INSTALL.md` and `app/server.py`, use
  that folder as-is (this covers Safari's auto-unzip) — skip unzipping.
- If both a zip and such a folder exist and the folder's `VERSION` differs
  from the zip's `VERSION`, the zip wins: unzip it into a fresh folder name
  (e.g. `keel-2/`) instead of overwriting the older folder, and
  use that fresh folder from here on.
- If neither a zip nor a matching folder is found, tell the user (in their
  own language) what to download (`keel-*.zip`, placed next to
  this file) and stop here.
- Never delete anything in any of these cases — `unzip -n` and picking a
  fresh folder name are enough to avoid collisions.

## 1. Check prerequisites

`cd` into the unzipped kit folder (the folder that contains this file and
`app/server.py`), then run:

```
python3 --version
python3 -c "import sys; assert sys.version_info >= (3, 9)"
git --version
```

- If `git --version` fails, tell the user to run `xcode-select --install`
  and stop here — do not continue until it succeeds.
- If either of the two `python3` checks fails, tell the user a Python 3.9
  or newer must come first on PATH (on macOS, `/usr/bin/python3` from the
  Command Line Tools satisfies it), and stop here — do not continue.
- Also check, and note in your report, whether these are present (missing
  ones are fine — the web page shows the user what is and isn't available):

```
command -v claude
command -v codex
command -v gh
test -d /Applications/Obsidian.app
test -d ~/Applications/Obsidian.app
```

## 2. Start the local server

### A note on the sandbox

Run the server **outside** any sandbox this agent or CLI has active. It
writes to `~/.keel`, `~/.claude`, `~/.codex`, and the user's chosen
KB folder, and it calls the GitHub API when the user connects tab 3 — a
sandboxed shell that blocks filesystem writes outside the repo or blocks
network access will make the server fail silently or half-work.

- Codex: request escalated permissions for this command.
- Claude Code with the sandbox on: run this command unsandboxed.

If your sandbox or permission policy blocks this command, **do not stop
here** — ask the user to approve running it outside the sandbox (Claude
Code: the user approves the permission prompt; Codex: request escalated
permissions) and continue with the rest of this file once they approve.

If the server does not behave as expected, check the log
(`/tmp/keel-install.log`) for `Permission denied` or `Operation not
permitted` — that is the sandbox, not a bug in the kit.

### Start it

From the unzipped kit folder, start the server as a background process,
capture its PID, and send its output to a log file — run this as a single
command (a split into two separate tool calls loses `$!` between them):

```
python3 -u app/server.py > /tmp/keel-install.log 2>&1 & echo $! > /tmp/keel-server.pid
```

`-u` (unbuffered) matters here — the log is being read from a redirected
file, not a terminal. You will use the PID file in step 6 to stop the
server.

The server opens the user's default browser itself as soon as it starts —
that is expected, do not treat it as a problem and do not open the URL
yourself.

If a previous run left a result file behind, the server moves it aside at
startup (`install-result.json` → `install-result.prev.json`) so step 4 does
not mistake a stale result for this run's.

Then read the URL the server printed — wait for the line with a short
bounded loop (up to ~10 s total) instead of a single fixed `sleep`:

```
for i in $(seq 20); do grep -qE '열기:|Open:' /tmp/keel-install.log && break; sleep 0.5; done
grep -E '열기:|Open:' /tmp/keel-install.log
```

If the loop finishes without the line ever appearing, treat startup as
failed.

The line looks like `Open / 열기: http://127.0.0.1:<port>/?t=<token>`. The URL
after that label is what you hand to the user — do not open it yourself,
do not fetch it, do not screenshot it. The token in the URL is required
for every request to the local server; without it the server refuses the
request.

The browser should have opened on its own (the server does this itself).
One rule, no branching on whether it actually did: tell the user the
browser should have opened, and always give them the URL above to open by
hand regardless — the same URL step 3's fixed message hands over next.

## 3. Hand the URL to the user and wait

Tell the user, in the user's own language, that the browser should have
opened already, and always give them the address and the four steps too,
regardless (with the real URL substituted):

```
The browser should have opened already. Here is the address too — open it and go through the four steps: <URL>
```

The page itself defaults to English with a Korean toggle in the top bar, so
this instruction does not depend on which language the page ends up in.

Then wait for the user to finish all four tabs in the page themselves
(Hooks · Knowledge Base · GitHub · Finish/Obsidian). Do not open the URL, do not
click anything in it, and do not choose any hook, KB location, or repo on
the user's behalf — those decisions belong to the user alone.

## 4. Poll for completion

The web page writes a result file when the user finishes tab 4. Poll for it
with this exact bounded loop (30 checks, 10 s apart — up to 5 minutes):

```
for i in $(seq 30); do test -f ~/.keel/install-result.json && { echo found; break; }; sleep 10; done
```

- Set your tool's timeout to at least 6 minutes when you run this command
  (Claude Code: `timeout: 360000`; Codex: `timeout_ms: 360000`) — the loop
  itself can run close to 5 minutes, and a shorter tool timeout will kill it
  before it reports anything.
- If the loop prints `found`, continue to step 5.
- If it finishes without printing `found`, run it again — up to 6 times in
  total (≈30 minutes of polling). If all 6 runs finish with no result file,
  **end your turn** with this line (do not keep looping past this budget in
  the same turn):

```
Let me know (in your own language) once you're done, e.g. "done".
```

  When the user responds, re-run the same loop before continuing.

## 5. Verify and report

Read the result file and confirm each item against the real filesystem:

```
cat ~/.keel/install-result.json
```

- `wiki_path` — confirm `<wiki_path>/index.md` exists.
- `hooks` — report which hook ids were installed for `claude` and for
  `codex` (per the file's contents; do not assume both were chosen).
- `skills` — report which KB skills were installed, out of `kb`, `kb-ingest`, `kb-lint`.
- `repos` — report which repos (if any) got a generated index under
  `<wiki_path>/repos/`.

Tell the user: hooks only take effect in a **new** CLI session — if
`claude` or `codex` hooks were installed, they must start a fresh session
(not the current one) for the hooks to load.

If Codex hooks were installed, remind the user of the "human only" Codex
step below — Codex will not run the hooks until the user trusts them
interactively.

## 6. Stop the server

The server has done its job once the result file exists. Stop the
background process you started in step 2, using the same sandbox
escalation as step 2 (Codex: escalated permissions; Claude Code: run
unsandboxed), then verify it actually stopped:

```
pid="$(cat /tmp/keel-server.pid)"
kill "$pid"
for i in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
ps -p "$pid" >/dev/null && echo still-running
```

The final `ps -p` command must print nothing. If it prints
`still-running`, the process did not stop — investigate before moving on.
If `kill` itself printed `Operation not permitted`, the sandbox
escalation from step 2 was not applied.

## [Human only] — steps only the human can do

These are not yours to perform. Point them out; do not attempt them:

- **Obsidian install/open** — if step 1 found no `/Applications/Obsidian.app`
  or `~/Applications/Obsidian.app`, tab 4 of the web page shows a
  `brew install --cask obsidian` command and a download link. Only the user
  can run the installer and approve any macOS security prompts. Opening the
  KB in Obsidian (the `obsidian://open?...` link tab 4 provides) is also a
  GUI action for the user.
- **Codex `/hooks` trust** — Codex will not execute newly installed hooks
  until the user reviews and trusts them once, interactively, by running
  `/hooks` inside a Codex session. This cannot be done from a script or by
  you.
- **GitHub token creation** — if the user wants tab 3 (GitHub history) and
  has no `gh` CLI login, they create a personal access token themselves
  (read-only `repo` scope is enough) and paste it into the page. You never
  see or handle this token.

## Privacy notes

- Any GitHub token the user provides is held in the running server's memory
  only. It is never written to disk, to `.git/config`, to a log file, or
  into any URL.
- Only commit metadata (dates, authors, subjects/titles, file paths touched)
  is read and written to the KB — never file contents.
- If the user turns on the optional LLM summary pass, commit titles (not
  file contents) are sent to whichever CLI's provider the user selected
  (`claude` or `codex`) to generate a short summary.

## Undo order (되돌리기 순서)

Follow this order — deleting `~/.keel` first, while hooks are still
registered in `~/.claude/settings.json` or `~/.codex/hooks.json`, makes every
one of those hooks exit 2 on its next run (the scripts they point to are
gone), which blocks the CLI until you fix it.

1. **Remove the hooks first**, while `~/.keel` still exists, using
   one of:
   - Start the server again (step 2 above). Keep every visible CLI
     checkbox (Claude Code, Codex, Grok) ticked in tab 1 — unticking one
     excludes that target from the uninstall call and can leave its hooks
     in place. Then use the **"Remove all kit hooks"** button (labeled
     "키트 훅 모두 제거" if the page is in Korean), with the **"Also remove
     skills"** checkbox ("스킬도 제거") ticked if you also want the
     installed KB skills removed.
   - Or restore by hand. For each of `~/.claude/settings.json`,
     `~/.codex/hooks.json`, and `~/.grok/hooks/keel.json`: the kit writes
     `<file>.bak-before-kit` when
     the file was missing at install time and no sentinel existed yet;
     restoring it is safe from the lockout but discards any later changes
     to that file. Otherwise, use the **newest** `*.bak-*` file next to it
     for which `grep -c '/.keel/hooks/' <backup>` prints `0` —
     that means the backup predates any kit hooks, so it is kit-free. A
     backup with a non-zero count was taken *after* a kit install and
     already contains kit hooks, so restoring one of those and then
     deleting `~/.keel` recreates the exit-2 lockout this section
     warns about. `~/.codex/config.toml` needs no restoring — it holds no kit
     hook commands, so it cannot cause the lockout; `hooks = true` is
     covered by the paragraph below. If a config file has neither a
     `.bak-before-kit` nor a `*.bak-*` at all, the kit created that file
     from nothing — there is no pre-kit state to restore, so use the
     button instead.
     Restore the Grok status line entry in `~/.grok/config.toml` from its
     pre-kit value if present. Remove Keel skills under `~/.grok/skills/`
     when removing skills by hand.
2. **Only then**, delete everything else the kit wrote:
   `rm -rf ~/.keel`. This does not delete the KB itself (the
   user's `wiki_path`, e.g. `~/knowledge-base`) or Obsidian.

`hooks = true` in `~/.codex/config.toml` is left in place by both the
uninstall button and this whole procedure — other, non-kit Codex hooks may
depend on that setting, so nothing here turns it off automatically. If you
are certain nothing else needs it, remove that line by hand.

## A note on the environment

If the agent or CLI running this file has its own global hooks that block
certain commands (git branch guards, permission prompts, etc.), those come
from that agent's own configuration — they are not part of this kit and
this file does not account for them.
