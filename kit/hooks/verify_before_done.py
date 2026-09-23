#!/usr/bin/env python3
from __future__ import annotations
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

DONE_RE = re.compile(
    r"(완료(했|되었|됐)|끝났|다 했|고쳤습니다|\bdone\b|\bcompleted?\b|\bfixed\b|all set)",
    re.IGNORECASE,
)
EDIT_NAMES = {"Edit", "Write", "MultiEdit"}
VERIFY_RE = re.compile(
    r"(test|pytest|unittest|jest|vitest|npm run|pnpm|yarn|go test|cargo (test|build|check)|"
    r"make|tsc|lint|build|curl)",
    re.IGNORECASE,
)


def run() -> int:
    payload = _common.read_payload()
    transcript_path = payload.get("transcript_path", "")
    msgs = _common.read_transcript(transcript_path) if transcript_path else []
    text = _common.last_assistant_text(msgs)

    if not DONE_RE.search(text):
        return 0

    turn = _common.last_turn(msgs)

    edited = False
    verified = False
    for msg in turn:
        for tool_name, tool_input in msg.get("tools", []):
            if tool_name in EDIT_NAMES:
                edited = True
            elif tool_name == "Bash" and isinstance(tool_input, dict):
                command = tool_input.get("command", "")
                if isinstance(command, str) and VERIFY_RE.search(command):
                    verified = True

    if not edited or verified:
        return 0

    if not _common.may_block_stop(payload, "verify-before-done"):
        return 0

    lang = _common.ui_lang(_common.load_config())
    reason = _common.text("verify_before_done", lang)
    return _common.block_stop(reason)


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
