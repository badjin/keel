#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common


_LOG_HINT = "~/.keel/state/auto-update.log"
_NOTICE_TEXT = {
    "en": {
        "text": (
            "[keel] The last automatic KB update was not applied: it would "
            "have removed or changed existing text in {path}. Your KB is "
            "unchanged. Details: " + _LOG_HINT
        ),
        "outside": (
            "[keel] The last automatic KB update was not applied: it wrote "
            "outside index.md and wiki/ ({path}). Your KB is unchanged. "
            "Details: " + _LOG_HINT
        ),
        "conflict": (
            "[keel] The last automatic KB update was not applied: the KB "
            "changed while it ran. Your KB is unchanged. Details: " + _LOG_HINT
        ),
        "count": (" ({count} updates)"),
    },
    "ko": {
        "text": (
            "[keel] 마지막 지식 베이스 자동 업데이트를 반영하지 않았습니다: {path} 의 기존 "
            "내용을 지우거나 바꾸려 했습니다. 지식 베이스는 그대로입니다. 자세한 내용: "
            + _LOG_HINT
        ),
        "outside": (
            "[keel] 마지막 지식 베이스 자동 업데이트를 반영하지 않았습니다: index.md 와 "
            "wiki/ 밖에 쓰려 했습니다({path}). 지식 베이스는 그대로입니다. 자세한 내용: "
            + _LOG_HINT
        ),
        "conflict": (
            "[keel] 마지막 지식 베이스 자동 업데이트를 반영하지 않았습니다: 업데이트하는 "
            "동안 지식 베이스가 바뀌었습니다. 지식 베이스는 그대로입니다. 자세한 내용: "
            + _LOG_HINT
        ),
        "count": (" ({count}건)"),
    },
}


def _notice_line(notice, language: str) -> str:
    if not notice:
        return ""
    table = _NOTICE_TEXT.get(language, _NOTICE_TEXT["en"])
    if notice.get("kind") == "conflict":
        line = table["conflict"]
    elif notice.get("problem") == "outside":
        line = table["outside"].format(path=notice.get("path", ""))
    else:
        line = table["text"].format(path=notice.get("path", ""))
    count = notice.get("count", 1)
    if isinstance(count, int) and count > 1:
        line += table["count"].format(count=count)
    return line


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
    try:
        import _wiki_guard
        notice_line = _notice_line(_wiki_guard.take_notice(_common.kit_home()), language)
    except Exception:
        notice_line = ""
    if notice_line:
        text = f"{text}\n{notice_line}"
        print(json.dumps({
            "systemMessage": notice_line,
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text},
        }))
    else:
        _common.emit_context("SessionStart", text)
    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
