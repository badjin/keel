#!/usr/bin/env python3
"""Create a handoff marker and start the detached terminal reset driver."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_common as hc


def _owns_tmux_pane(pane: str) -> bool:
    try:
        result = subprocess.run(["tmux", "display-message", "-p", "-t", pane, "#{pane_pid}"],
                                capture_output=True, text=True, check=True)
        pane_pid = int(result.stdout.strip())
        pid = os.getpid()
        while pid > 1:
            if pid == pane_pid:
                return True
            parent = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                    capture_output=True, text=True, check=True)
            pid = int(parent.stdout.strip())
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False
    return False


def start(handoff: Path, sid: str, target: str, cwd: Path) -> tuple[Path, bool]:
    handoff = handoff.resolve()
    cwd = cwd.resolve()
    if (not re.fullmatch(r"[A-Za-z0-9_-]+", sid) or target not in ("claude", "codex", "grok")
            or not handoff.is_file() or handoff.parent != cwd / ".keel" / "handoff"):
        raise ValueError("invalid handoff path or CLI")
    gitignore = cwd / ".keel" / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    marker = hc.home() / "state" / "auto-handoff" / f"{sid}.json"
    restore_target = "claude" if target == "grok" and hc.read_json(hc.home() / "config.json").get("grok_via_claude") else target
    hc.write_json(marker, {"cwd": str(cwd), "handoff": str(handoff), "ts": time.time(),
                           "target": restore_target, "pane": os.environ.get("HERDR_PANE_ID") or os.environ.get("TMUX_PANE")})
    driver = Path(__file__).with_name("handoff_driver.sh")
    if os.environ.get("HERDR_ENV") and os.environ.get("HERDR_PANE_ID"):
        terminal = "herdr"
        pane = os.environ["HERDR_PANE_ID"]
    elif os.environ.get("TMUX") and os.environ.get("TMUX_PANE"):
        terminal = "tmux"
        pane = os.environ["TMUX_PANE"]
        if not _owns_tmux_pane(pane):
            return marker, False
    else:
        return marker, False
    subprocess.Popen(["sh", str(driver), terminal, pane, target, sid, str(marker), str(handoff), hc.language()],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return marker, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", required=True, type=Path)
    parser.add_argument("--sid", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--cwd", required=True, type=Path)
    args = parser.parse_args()
    try:
        _, automatic = start(args.handoff, args.sid, args.target, args.cwd)
    except ValueError as exc:
        parser.error(str(exc))
    lang = hc.language()
    if automatic:
        print("The terminal will reset and resume automatically." if lang == "en" else "터미널이 자동으로 리셋하고 이어갑니다.")
    elif args.target == "grok":
        print("Type /new, then ask Grok to restore the pending handoff. Grok does not deliver SessionStart hook output to the agent." if lang == "en" else
              "/new를 입력한 뒤 Grok에게 대기 중인 핸드오프를 복원하라고 요청하세요. Grok은 SessionStart 훅 출력을 에이전트에게 전달하지 않습니다.")
    else:
        print("Type /clear (or /new) to continue — the next session resumes automatically." if lang == "en" else
              "계속하려면 /clear(또는 /new)를 입력하세요 — 다음 세션이 자동으로 이어갑니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
