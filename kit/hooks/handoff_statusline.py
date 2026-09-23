#!/usr/bin/env python3
"""Record context usage and preserve a pre-existing status line's output."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_common as hc


def run() -> int:
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw)
        sid = payload.get("session_id")
        context = payload.get("context_window") or {}
        pct = context.get("used_percentage")
        if sid and isinstance(pct, (int, float)):
            hc.write_json(hc.home() / "state" / "context" / f"{sid}.json", {
                "session_id": sid, "used_percentage": pct,
                "context_window_size": context.get("context_window_size"),
                "ts": time.time(),
            })
    except (ValueError, TypeError):
        pass
    target = sys.argv[1] if len(sys.argv) > 1 else "claude"
    original = hc.read_json(hc.home() / "state" / "statusline.json").get(target)
    command = original.get("command") if isinstance(original, dict) else None
    if command:
        result = subprocess.run(command, shell=True, input=raw, stdout=subprocess.PIPE)
        sys.stdout.buffer.write(result.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(run())
