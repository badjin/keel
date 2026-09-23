#!/usr/bin/env python3
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

GUARDED_NAMES = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MAIN_BRANCHES = {"main", "master"}


def _existing_parent(path: str) -> str | None:
    p = Path(path).parent
    while True:
        if p.exists():
            return str(p)
        parent = p.parent
        if parent == p:
            return None
        p = parent


def _branch_for(file_path: str) -> str | None:
    parent = _existing_parent(file_path)
    if parent is None:
        return None
    try:
        top_proc = subprocess.run(
            ["git", "-C", parent, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if top_proc.returncode != 0:
            return None
        top = top_proc.stdout.strip()
        if not top:
            return None
        branch_proc = subprocess.run(
            ["git", "-C", top, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if branch_proc.returncode != 0:
            return None
        return branch_proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def run() -> int:
    payload = _common.read_payload()
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    cwd = payload.get("cwd", ".")
    if not isinstance(cwd, str):
        cwd = "."

    for name, ti in _common.normalize_tool(tool_name, tool_input, cwd):
        if name not in GUARDED_NAMES:
            continue
        if not isinstance(ti, dict):
            continue
        file_path = ti.get("file_path")
        if not file_path:
            continue
        branch = _branch_for(file_path)
        if branch in MAIN_BRANCHES:
            lang = _common.ui_lang(_common.load_config())
            reason = _common.text("main_branch_guard", lang, branch=branch)
            return _common.deny_tool(reason)

    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
