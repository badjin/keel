#!/usr/bin/env python3
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common


def run() -> int:
    payload = _common.read_payload()
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        return 0

    if len(prompt.strip()) < 200:
        return 0

    lang = _common.ui_lang(_common.load_config())
    _common.emit_context("UserPromptSubmit", _common.text("long_prompt_brief", lang))
    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
