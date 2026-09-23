#!/usr/bin/env python3
"""Stop hook for context threshold and turn-finished signalling."""
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
    sid = payload.get("session_id") or payload.get("sessionId")
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        return 0
    state = hc.home() / "state"
    stopped = state / "auto-handoff" / f"{sid}.stopped"
    stopped.parent.mkdir(parents=True, exist_ok=True)
    stopped.write_text(str(time.time()), encoding="utf-8")
    if payload.get("stop_hook_active") or payload.get("stopHookActive"):
        return 0
    target = sys.argv[1] if len(sys.argv) > 1 else "claude"
    if target == "claude" and ("sessionId" in payload or "/.grok/" in payload.get("transcript_path", "")):
        target = "grok"
    if target == "grok" and payload.get("reason") not in (None, "end_turn"):
        return 0
    config = hc.read_json(hc.home() / "config.json")
    if target not in config.get("handoff_targets", [target]):
        return 0
    pct = hc.usage(sid, payload.get("transcript_path", ""), target)
    if pct is None:
        return 0
    limit = config.get("handoff_threshold", 60)
    reg_path = state / "context-handoff" / f"{sid}.json"
    reg = hc.read_json(reg_path)
    if time.time() < reg.get("grace_until", 0) or pct < limit:
        return 0
    previous = reg.get("pct")
    if previous is not None and pct < previous + 15:
        return 0
    reg["pct"] = pct
    hc.write_json(reg_path, reg)
    reason = (
        f"Context {pct:.0f}% reached the {limit}% limit. Session: {sid}; CLI: {target}; cwd: {payload.get('cwd', '')}. Run the handoff skill's auto action now. "
        "Save the current work before resetting; do not ask for confirmation."
        if hc.language() == "en" else
        f"컨텍스트 {pct:.0f}%가 임계치 {limit}%에 도달했습니다. 세션: {sid}; CLI: {target}; cwd: {payload.get('cwd', '')}. handoff 스킬의 자동 동작을 지금 실행하세요. "
        "리셋 전에 현재 작업을 저장하고 확인을 묻지 마세요."
    )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(run())
