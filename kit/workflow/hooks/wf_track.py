#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

keel_home = _common.kit_home()
sys.path.insert(0, str(keel_home))
from workflow import work


def run() -> int:
    payload = _common.read_payload()
    config = _common.load_config(keel_home)
    if _common.is_nested() or not config or not config.get("wiki_path"):
        return 0
    workflow_config = config.get("workflow") or {}
    if not isinstance(workflow_config, dict) or not workflow_config.get("installed"):
        return 0

    kb = Path(config["wiki_path"])
    configured_root = work.work_root(kb)
    root = configured_root.resolve()
    cwd = payload.get("cwd", ".")
    folder = None
    for name, ti in _common.normalize_tool(payload.get("tool_name", ""), payload.get("tool_input", {}), cwd):
        if not isinstance(ti, dict):
            continue
        for candidate in (ti.get("file_path"), ti.get("path")):
            if not isinstance(candidate, str):
                continue
            path = Path(candidate)
            path = (path if path.is_absolute() else Path(cwd) / path).resolve()
            try:
                item_id = path.relative_to(root).parts[0]
            except (ValueError, IndexError):
                continue
            possible = configured_root / item_id
            if possible.is_dir():
                folder = possible
                break
        if folder:
            break
        if name == "Bash" and isinstance(ti.get("command"), str):
            for command_root in {str(root), str(configured_root)}:
                for match in re.finditer(re.escape(command_root) + r"/([^/\s;|&'\"]+)", ti["command"]):
                    possible = configured_root / match.group(1)
                    if possible.is_dir():
                        folder = possible
                        break
                if folder:
                    break
            if folder:
                break
    if folder:
        session_id = payload.get("session_id", "")
        if session_id:
            session_file = keel_home / "state" / "workflow" / "sessions" / f"{session_id}.json"
            work.fsutil.atomic_write_json(session_file, {"folder": str(folder), "updated": work.fsutil.now_iso()})
            cli = "codex" if sys.argv[1:] == ["codex"] else "claude"
            work.add_session(folder, session_id, cli, payload.get("transcript_path", ""))
    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
