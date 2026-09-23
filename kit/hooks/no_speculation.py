#!/usr/bin/env python3
from __future__ import annotations
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
QUOTE_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)

SPECULATION_PATTERNS = [
    r"아마(도)?",
    r"것 같",
    r"것으로 보입니다",
    r"추정",
    r"\bprobably\b",
    r"\blikely\b",
    r"\bI think\b",
    r"\bseems? to\b",
    r"\bmight be\b",
]
SPECULATION_RE = re.compile("|".join(SPECULATION_PATTERNS), re.IGNORECASE)


def _clean(text: str) -> str:
    text = FENCE_RE.sub("", text)
    text = QUOTE_LINE_RE.sub("", text)
    return text


def run() -> int:
    payload = _common.read_payload()
    transcript_path = payload.get("transcript_path", "")
    msgs = _common.read_transcript(transcript_path) if transcript_path else []
    text = _common.last_assistant_text(msgs)
    cleaned = _clean(text)

    matches = SPECULATION_RE.findall(cleaned)
    if not matches:
        matched_texts = []
    else:
        matched_texts = [m.group(0) for m in SPECULATION_RE.finditer(cleaned)]

    if not matched_texts:
        return 0

    if not _common.may_block_stop(payload, "no-speculation"):
        return 0

    joined = ", ".join(matched_texts)
    lang = _common.ui_lang(_common.load_config())
    reason = _common.text("no_speculation", lang, matches=joined)
    return _common.block_stop(reason)


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
