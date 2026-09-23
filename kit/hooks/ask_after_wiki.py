#!/usr/bin/env python3
from __future__ import annotations
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
QUESTION_TAIL_RE = re.compile(r"(까요|나요|습니까|할까요|주세요)\s*[?.]?$")


def _is_question_line(line: str) -> bool:
    line = line.strip()
    if not line:
        return False
    if line.endswith("?"):
        return True
    return bool(QUESTION_TAIL_RE.search(line))


def _last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line
    return ""


def _under_wiki(path_str: str, wiki_path_str: str) -> bool:
    try:
        target = os.path.normpath(os.path.abspath(path_str))
        wiki = os.path.normpath(os.path.abspath(wiki_path_str))
    except Exception:
        return False
    return target == wiki or target.startswith(wiki + os.sep)


def _turn_touched_wiki(turn, wiki_path: str) -> bool:
    for msg in turn:
        for tool_name, tool_input in msg.get("tools", []):
            if not isinstance(tool_input, dict):
                continue
            if tool_name == "Read":
                fp = tool_input.get("file_path")
                if fp and _under_wiki(fp, wiki_path):
                    return True
            elif tool_name == "Bash":
                command = tool_input.get("command", "")
                if isinstance(command, str) and wiki_path and wiki_path in command:
                    return True
            elif tool_name in ("Grep", "Glob"):
                path = tool_input.get("path")
                if path and _under_wiki(path, wiki_path):
                    return True
    return False


def run() -> int:
    payload = _common.read_payload()
    config = _common.load_config()
    if config is None:
        return 0

    wiki_path = config.get("wiki_path", "")
    if not wiki_path:
        return 0

    transcript_path = payload.get("transcript_path", "")
    msgs = _common.read_transcript(transcript_path) if transcript_path else []
    text = _common.last_assistant_text(msgs)
    cleaned = FENCE_RE.sub("", text)

    last_line = _last_nonempty_line(cleaned)
    if not _is_question_line(last_line):
        return 0

    turn = _common.last_turn(msgs)
    if _turn_touched_wiki(turn, wiki_path):
        return 0

    if not _common.may_block_stop(payload, "ask-after-wiki"):
        return 0

    lang = _common.ui_lang(config)
    reason = _common.text("ask_after_wiki", lang, wiki_path=wiki_path)
    return _common.block_stop(reason)


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
