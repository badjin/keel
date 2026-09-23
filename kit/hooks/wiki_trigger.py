#!/usr/bin/env python3
from __future__ import annotations
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

WIKI_RE = re.compile(r"(위키|wiki)", re.IGNORECASE)
ACTION_RE = re.compile(
    r"(추가|업데이트|정리|저장|반영|넣어|기록|add|update|save|record|organi[sz]e)",
    re.IGNORECASE,
)


def run() -> int:
    payload = _common.read_payload()
    config = _common.load_config()
    if config is None:
        return 0

    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        return 0

    if not (WIKI_RE.search(prompt) and ACTION_RE.search(prompt)):
        return 0

    wiki_path = config.get("wiki_path", "")
    lang = _common.ui_lang(config)
    text = _common.text("wiki_trigger", lang, wiki_path=wiki_path)
    _common.emit_context("UserPromptSubmit", text)
    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
