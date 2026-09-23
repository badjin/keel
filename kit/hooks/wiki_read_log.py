#!/usr/bin/env python3
from __future__ import annotations
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

FORBIDDEN_CHARS = set("|;&><$`")
SIMPLE_CMD_RE = re.compile(r"^(cat|head|tail|less|bat)\s+(\S+)\s*$")
SED_CMD_RE = re.compile(r"^sed\s+-n\s+'[^']*'\s+(\S+)\s*$")


def _extract_path(command: str) -> str | None:
    if any(c in command for c in FORBIDDEN_CHARS):
        return None
    stripped = command.strip()
    m = SIMPLE_CMD_RE.match(stripped)
    if m:
        return m.group(2)
    m = SED_CMD_RE.match(stripped)
    if m:
        return m.group(1)
    return None


def _under_wiki(path_str: str, wiki_path_str: str) -> bool:
    try:
        target = os.path.normpath(os.path.abspath(path_str))
        wiki = os.path.normpath(os.path.abspath(wiki_path_str))
    except Exception:
        return False
    return target == wiki or target.startswith(wiki + os.sep)


def run() -> int:
    payload = _common.read_payload()
    config = _common.load_config()
    if config is None:
        return 0

    wiki_path = config.get("wiki_path", "")
    if not wiki_path:
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    cwd = payload.get("cwd", ".")
    if not isinstance(cwd, str):
        cwd = "."
    session_id = payload.get("session_id", "")

    target_path = None
    for name, ti in _common.normalize_tool(tool_name, tool_input, cwd):
        if not isinstance(ti, dict):
            continue
        if name == "Read":
            fp = ti.get("file_path")
            if fp:
                target_path = fp
                break
        elif name == "Bash":
            command = ti.get("command")
            if isinstance(command, str):
                p = _extract_path(command)
                if p:
                    target_path = p if os.path.isabs(p) else str(Path(cwd) / p)
                    break

    if not target_path:
        return 0

    if not _under_wiki(target_path, wiki_path):
        return 0

    kh = _common.kit_home()
    state_dir = kh / "state"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"{timestamp}\t{session_id}\t{target_path}\n"
        with open(state_dir / "wiki-reads.log", "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass

    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
