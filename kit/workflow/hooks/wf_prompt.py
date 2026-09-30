#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

keel_home = _common.kit_home()
sys.path.insert(0, str(keel_home))
from workflow import approval, docs, work


def _origin(cwd: str) -> str:
    result = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, check=False)
    return str(Path(result.stdout.strip()).resolve()) if result.returncode == 0 else str(Path(cwd).resolve())


def run() -> int:
    payload = _common.read_payload()
    config = _common.load_config(keel_home)
    if _common.is_nested() or not config or not config.get("wiki_path"):
        return 0
    workflow_config = config.get("workflow") or {}
    if not isinstance(workflow_config, dict) or not workflow_config.get("installed"):
        return 0
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or _common._is_synthetic_user_text(prompt):
        return 0

    kb = Path(config["wiki_path"])
    lang = workflow_config.get("language", "en")
    lines = []
    name = approval.match_prompt(prompt, [docs.text("approve_word", "en"), docs.text("approve_word", lang)])
    if name:
        result, item_id = approval.approve(kb, keel_home, name, payload.get("session_id", ""), prompt)
        key = {"approved": "approved_msg", "none": "nothing_awaits",
               "ambiguous": "ambiguous_slug", "stale": "stale_intent"}[result]
        lines.append(docs.text(key, lang, id=item_id, name=name,
                               hash12=docs.intent_hash((work.work_root(kb) / item_id / "intent.md").read_text(encoding="utf-8"))[:12]
                               if result == "approved" else ""))

    lines.append(docs.text("grade_block", lang, keel_wf=workflow_config.get("keel_wf", "")))
    session_id = payload.get("session_id", "")
    session_file = keel_home / "state" / "workflow" / "sessions" / f"{session_id}.json"
    owned = None
    if session_id and session_file.is_file():
        try:
            owned = Path(json.loads(session_file.read_text(encoding="utf-8"))["folder"])
        except (ValueError, KeyError, TypeError):
            pass
    reminded = False
    if owned and (owned / "intent.md").is_file():
        intent = (owned / "intent.md").read_text(encoding="utf-8")
        status = docs.get_status(intent)
        if status != "done":
            problem_text = docs.sections(intent).get("Problem", "").strip()
            problem = re.split(r"(?<=[.!?])\s", problem_text, maxsplit=1)[0]
            next_step = {"draft": "finish the intent and run critic intent",
                         "awaiting-approval": f"wait for the user to approve on the approval page or type approve {work.load_state(owned).get('slug', owned.name)}",
                         "approved": "spec / plan / run per keel-run"}.get(status, "")
            lines.append(docs.text("work_reminder", lang, id=owned.name, problem=problem,
                                   status=status, next=next_step, path=str(owned)))
            reminded = True
    if not reminded:
        cwd = payload.get("cwd", ".")
        origin = _origin(cwd)
        matches = [folder for folder in work.open_items(kb)
                   if work.load_state(folder).get("origin") == origin]
        if len(matches) == 1:
            folder = matches[0]
            status = docs.get_status((folder / "intent.md").read_text(encoding="utf-8"))
            lines.append(docs.text("work_hint", lang, path=str(folder), status=status))
    _common.emit_context("UserPromptSubmit", "\n".join(lines))
    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
