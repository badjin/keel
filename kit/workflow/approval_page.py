from __future__ import annotations

import html
import json
import os
from pathlib import Path
import secrets
from string import Template
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

from . import approval, docs, fsutil, work


def page_html(intent_text: str, slug: str, intent_hash: str) -> str:
    labels = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
    labels_json = json.dumps(labels, ensure_ascii=False).replace("</", "<\\/")
    return Template("""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title></title>
  <style>
    body { font: 16px/1.5 system-ui, sans-serif; max-width: 52rem; margin: 2rem auto; padding: 0 1rem; color: #17202a; }
    header { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
    h1 { margin: 0; font-size: 1.6rem; }
    .slug { margin: .25rem 0 1rem; color: #52606d; }
    .languages { display: flex; gap: .4rem; }
    button { padding: .6rem .9rem; border: 1px solid #52606d; border-radius: .4rem; background: white; cursor: pointer; }
    button:disabled { cursor: default; opacity: .55; }
    button[aria-pressed="true"], #approve { background: #174a7e; border-color: #174a7e; color: white; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; background: #f4f6f8; padding: 1rem; border-radius: .4rem; }
    .action-bar { position: sticky; bottom: 0; background: white; border-top: 1px solid #52606d; padding: .5rem 1rem; margin: 0 -1rem; }
    .actions { display: flex; gap: .6rem; flex-wrap: wrap; margin: .5rem 0; }
    #change-form[hidden] { display: none; }
    textarea { display: block; box-sizing: border-box; width: 100%; min-height: 5rem; margin: .4rem 0 1rem; font: inherit; }
  </style>
</head>
<body>
  <header>
    <div><h1 id="heading"></h1><p class="slug">$slug</p></div>
    <nav class="languages">
      <button type="button" data-lang="en" aria-pressed="true"></button>
      <button type="button" data-lang="ko" aria-pressed="false"></button>
    </nav>
  </header>
  <pre>$intent_text</pre>
  <div class="action-bar">
    <div class="actions">
      <button type="button" id="approve"></button>
      <button type="button" id="request-changes"></button>
    </div>
    <div id="change-form" hidden>
      <label for="note" id="note-label"></label>
      <textarea id="note"></textarea>
      <button type="button" id="send-changes"></button>
    </div>
    <p id="message" role="status" aria-live="polite"></p>
  </div>
  <script type="application/json" id="labels">$labels_json</script>
  <script>
    const labels = JSON.parse(document.getElementById('labels').textContent);
    const hash = $intent_hash;
    const approve = document.getElementById('approve');
    const requestChanges = document.getElementById('request-changes');
    const sendChanges = document.getElementById('send-changes');
    const changeForm = document.getElementById('change-form');
    const message = document.getElementById('message');
    const buttons = [approve, requestChanges, sendChanges];
    let language = 'en';
    let result = null;
    try {
      if (localStorage.getItem('approval-page-language') === 'ko') language = 'ko';
    } catch (_) {}

    function render() {
      const words = labels[language];
      document.documentElement.lang = language;
      document.title = words.title;
      document.getElementById('heading').textContent = words.intent_heading;
      approve.textContent = words.approve;
      requestChanges.textContent = words.request_changes;
      sendChanges.textContent = words.send_changes;
      document.getElementById('note-label').textContent = words.note_label;
      document.querySelectorAll('[data-lang]').forEach(button => {
        button.textContent = labels[button.dataset.lang].toggle;
        button.setAttribute('aria-pressed', String(button.dataset.lang === language));
      });
      if (result) message.textContent = words[result + '_msg'] || words.error_msg;
    }

    document.querySelectorAll('[data-lang]').forEach(button => {
      button.addEventListener('click', () => {
        language = button.dataset.lang;
        try { localStorage.setItem('approval-page-language', language); } catch (_) {}
        render();
      });
    });

    async function decide(decision) {
      buttons.forEach(button => { button.disabled = true; });
      let response;
      try {
        response = await fetch('decide', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ decision, hash, note: document.getElementById('note').value })
        });
      } catch (_) {
        // The server is gone, so a retry cannot work: keep the buttons disabled.
        result = 'closed';
        render();
        return;
      }
      try {
        if (!response.ok) throw new Error('Request failed');
        const data = await response.json();
        result = data.result;
        render();
      } catch (_) {
        result = 'error';
        render();
        buttons.forEach(button => { button.disabled = false; });
      }
    }

    approve.addEventListener('click', () => decide('approve'));
    requestChanges.addEventListener('click', () => { changeForm.hidden = false; });
    sendChanges.addEventListener('click', () => decide('changes'));
    render();
  </script>
</body>
</html>
""").substitute(
        slug=html.escape(slug),
        intent_text=html.escape(intent_text),
        labels_json=labels_json,
        intent_hash=json.dumps(intent_hash).replace("</", "<\\/"),
    )


def decide(kb: Path, keel_home: Path, folder: Path, decision: str, shown_hash: str, note: str = "") -> str:
    intent_path = folder / "intent.md"
    original = intent_path.read_text(encoding="utf-8")
    current = docs.intent_hash(original)
    if shown_hash != current:
        return "stale"

    if decision == "approve":
        outcome, _ = approval.approve(
            kb, keel_home, folder.name, "approval-page", "approval page: approve", via="the approval page"
        )
        return "none" if outcome == "ambiguous" else outcome

    if decision == "changes":
        if docs.get_status(original) != "awaiting-approval":
            return "none"
        state = work.load_state(folder)
        reviewed = state.get("reviews", {}).get("intent", {})
        if reviewed.get("verdict") != "PASS" or reviewed.get("hashes", {}).get("intent") != current:
            return "stale"

        note = " ".join(note.splitlines()).strip()[:500]
        at = fsutil.now_iso()

        def record_changes(state: dict) -> None:
            state["changes_requested"] = {"note": note, "at": at}
            state["reviews"].pop("intent", None)
            state.setdefault("review_fails", {})["intent"] = 0

        # State goes first so a failure after this point cannot leave a draft intent that still holds its review.
        work.update_state(folder, record_changes)
        line = f"{at[:10]} changes requested on the approval page"
        updated = docs.add_changelog(docs.set_status(original, "draft"), f"{line}: {note}" if note else line)
        fsutil.atomic_write_text(intent_path, updated)
        return "changes"

    raise ValueError(decision)


def serve(
    kb: Path, keel_home: Path, folder: Path, *, announce,
    open_browser: bool = True, idle_seconds: float = 1800 + 120, poll_seconds: float = 2.0,
) -> str:
    if os.environ.get("KEEL_PAGE_FORCE_BIND_FAIL") == "1":
        raise PermissionError("Approval page binding disabled")

    token = secrets.token_urlsafe(16)
    slug = work.load_state(folder)["slug"]
    decision_lock = threading.Lock()
    stopped = threading.Event()
    result = {"value": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def _check(self, path: str) -> bool:
            if self.path != path:
                self.send_error(404)
                return False
            host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != host or self.headers.get("Origin", f"http://{host}") != f"http://{host}":
                self.send_error(403)
                return False
            return True

        def do_GET(self) -> None:
            if not self._check(f"/{token}/"):
                return
            intent_text = (folder / "intent.md").read_text(encoding="utf-8")
            body = page_html(intent_text, slug, docs.intent_hash(intent_text)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Keel-Approval-Page", slug)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if not self._check(f"/{token}/decide"):
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
                if not 0 <= length <= 8192:
                    raise ValueError("body length")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("payload")
                decision = payload["decision"]
                shown_hash = payload["hash"]
                note = payload.get("note", "")
                if decision not in ("approve", "changes") or not all(
                    isinstance(item, str) for item in (shown_hash, note)
                ):
                    raise ValueError("fields")
            except (ValueError, KeyError, UnicodeDecodeError):
                self.send_error(400)
                return

            with decision_lock:
                outcome = decide(kb, keel_home, folder, decision, shown_hash, note)
                if outcome in ("approved", "changes"):
                    result["value"] = outcome
            body = json.dumps({"result": outcome}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            if outcome in ("approved", "changes"):
                stopped.set()
                self.server.shutdown()

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/{token}/"
        deadline = time.monotonic() + idle_seconds

        def watch() -> None:
            while not stopped.wait(poll_seconds):
                if result["value"] is not None:
                    continue
                try:
                    status = docs.get_status((folder / "intent.md").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    status = None
                if status != "awaiting-approval" or time.monotonic() >= deadline:
                    stopped.set()
                    server.shutdown()
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            announce(url)
            if open_browser and os.environ.get("KEEL_NO_BROWSER") != "1":
                webbrowser.open(url)
            server.serve_forever(poll_interval=0.1)
        finally:
            stopped.set()
            watcher.join()

    if result["value"] is not None:
        return result["value"]
    status = docs.get_status((folder / "intent.md").read_text(encoding="utf-8"))
    return "approved" if status == "approved" else "changes" if status == "draft" else "timeout"
