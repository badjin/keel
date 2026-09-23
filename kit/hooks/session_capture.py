#!/usr/bin/env python3
from __future__ import annotations
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common


def run() -> int:
    payload = _common.read_payload()
    config = _common.load_config()
    if config is None:
        return 0

    wiki_path = config.get("wiki_path", "")
    if not wiki_path:
        return 0

    transcript_path = payload.get("transcript_path", "")
    if transcript_path:
        msgs, source = _common.read_transcript_with_source(transcript_path)
    else:
        msgs, source = [], "claude"

    user_texts = [
        m["text"].strip() for m in msgs
        if m.get("role") == "user" and (m.get("text") or "").strip()
    ]
    if len(user_texts) < 2:
        return 0

    last_text = _common.last_assistant_text(msgs)

    session_id = payload.get("session_id", "") or ""
    sid8 = session_id[:8] if session_id else "nosid"
    now = datetime.now()
    base_name = now.strftime("%Y-%m-%d-%H-%M") + "-" + sid8

    sessions_dir = Path(wiki_path) / "raw" / "sessions"
    try:
        sessions_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0

    filename = base_name + ".md"
    n = 2
    while (sessions_dir / filename).exists():
        filename = f"{base_name}-{n}.md"
        n += 1

    iso_now = datetime.now(timezone.utc).isoformat()
    cwd = payload.get("cwd", "")
    req_lines = "\n".join(
        "- " + t.replace("\n", " ")[:500] for t in user_texts
    )
    ui_language = _common.ui_lang(config)
    request_header = _common.session_capture_header("request", ui_language)
    last_answer_header = _common.session_capture_header("last_answer", ui_language)
    content = (
        "---\n"
        "status: unprocessed\n"
        f"date: {iso_now}\n"
        f"cwd: {cwd}\n"
        f"cli: {source}\n"
        "---\n"
        "\n"
        f"{request_header}\n"
        f"{req_lines}\n"
        "\n"
        f"{last_answer_header}\n"
        f"{last_text[:2000]}\n"
    )
    try:
        (sessions_dir / filename).write_text(content, encoding="utf-8")
    except OSError:
        pass

    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
