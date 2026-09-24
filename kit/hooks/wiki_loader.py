#!/usr/bin/env python3
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common


def _index_head(wiki_path: str, no_index_text: str) -> str:
    index_path = os.path.join(wiki_path, "index.md")
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            lines = []
            for i, line in enumerate(fh):
                if i >= 40:
                    break
                lines.append(line.rstrip("\n"))
        return "\n".join(lines)
    except Exception:
        return no_index_text


def run() -> int:
    _common.read_payload()
    config = _common.load_config()
    if config is None:
        return 0

    try:
        import _maintenance
        _maintenance.check()
    except Exception:
        pass

    wiki_path = config.get("wiki_path", "")
    language = _common.ui_lang(config)

    if language == "en":
        header = "=== Knowledge Base ==="
        path_label = "Path:"
        rules_label = "Rules:"
        rules_text = (
            "When asked to add or update the wiki, follow the kb-ingest skill. "
            "Before answering, look for relevant wiki pages first."
        )
        no_index = "(no index.md)"
        head_label = "--- index.md (head) ---"
    else:
        header = "=== 지식 베이스 ==="
        path_label = "경로:"
        rules_label = "규칙:"
        rules_text = (
            "위키에 추가·수정 요청이 오면 kb-ingest 스킬을 따른다. "
            "답하기 전에 관련 위키 페이지를 먼저 찾아 읽는다."
        )
        no_index = "(index.md 없음)"
        head_label = "--- index.md (앞부분) ---"

    head = _index_head(wiki_path, no_index)

    text = (
        f"{header}\n"
        f"{path_label} {wiki_path}\n"
        f"{rules_label} {rules_text}\n"
        f"{head_label}\n"
        f"{head}"
    )
    _common.emit_context("SessionStart", text)
    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
