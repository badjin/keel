#!/usr/bin/env python3
"""SessionStart hook that identifies one unclaimed handoff for this cwd."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_common as hc


def run() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    cwd = payload.get("cwd")
    if not cwd:
        return 0
    target = sys.argv[1] if len(sys.argv) > 1 else None
    found = hc.marker_for(cwd, target)
    if not found:
        return 0
    marker_path, marker = found
    handoff = Path(marker["handoff"])
    sid = payload.get("session_id") or payload.get("sessionId")
    if isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        hc.write_json(hc.home() / "state" / "context-handoff" / f"{sid}.json",
                      {"grace_until": time.time() + 900})
    hc.write_json(Path(str(marker_path) + ".claimed"), {"sid": sid, "ts": time.time()})
    reason = (
        f"Restore without asking: read {handoff}, move it to .keel/handoff/active/, "
        f"delete {marker_path}, and continue the unfinished work. "
        "Use the handoff skill's restore action."
        if hc.language() == "en" else
        f"묻지 말고 복원하세요: {handoff} 파일을 읽고 .keel/handoff/active/로 옮긴 뒤 "
        f"{marker_path} 삭제하고 남은 작업을 이어가세요. "
        "handoff 스킬의 복원 동작을 따르세요."
    )
    print(reason)
    return 0


if __name__ == "__main__":
    sys.exit(run())
