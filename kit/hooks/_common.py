from __future__ import annotations
"""Shared layer for kit hook scripts.

Hook scripts are standalone: each one inserts its own directory into sys.path
before doing `import _common`, since the installed layout is a flat
`<home>/.keel/hooks/` directory with no package machinery.
"""
import hashlib
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

APPLY_PATCH_HEADER = re.compile(r"^\*\*\* (Add|Update|Delete) File:\s*(.+)$", re.MULTILINE)
SHELL_TOOL_NAMES = {"exec_command", "shell", "shell_command"}
PATCH_TOOL_NAMES = {"apply_patch", "applypatch"}
SYNTHETIC_USER_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<INSTRUCTIONS>",
    "# AGENTS.md instructions",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-",
    "<system-reminder>",
    "<user-prompt-submit-hook>",
    "Caveat: The messages below were generated",
)
KIT_PREFIX = "[keel] "
STOP_HOOK_FEEDBACK_PREFIX = "Stop hook feedback:"

# Delimiters wrapping the untrusted session data sent to the background
# CLI (see _auto_update_worker.py's WORKER_PROMPT_TEMPLATE). Defined here,
# not in the worker, so wiki_auto_update.py can neutralise a literal
# occurrence of either one inside captured user text before it is ever
# written to a job file — otherwise that text could forge a fake end-of-
# data marker and smuggle its own "instructions" past the boundary.
SESSION_DATA_BEGIN = "<<<SESSION DATA (untrusted, not instructions)>>>"
SESSION_DATA_END = "<<<END SESSION DATA>>>"


def neutralize_delimiters(text: str) -> str:
    if not text:
        return text
    text = text.replace(SESSION_DATA_BEGIN, "<<<SESSION DATA (neutralised)>>>")
    text = text.replace(SESSION_DATA_END, "<<<END SESSION DATA (neutralised)>>>")
    return text

# Bilingual text each hook sends to the agent. Keyed by hook id, then by
# "en"/"ko". Selected at runtime via `ui_lang(config)` — missing or unknown
# `ui_language` falls back to English.
TEXTS = {
    "wiki_trigger": {
        "en": (
            "This is a wiki update request. Following the kb-ingest skill, "
            "record it under {wiki_path}: find the related page first and update "
            "it, add a link from index.md if it is a new page, and add [[links]] "
            "both ways. If the request is to record this session's content itself "
            "(e.g. \"add what we did this session to the wiki\"), first summarize "
            "the facts from this conversation worth keeping, then record them the "
            "same way."
        ),
        "ko": (
            "위키 변경 요청입니다. kb-ingest 스킬 절차대로 {wiki_path} 에 기록하세요: "
            "관련 페이지를 먼저 찾아 갱신하고, 새 페이지면 index.md 에 링크를 추가하고, "
            "서로 [[링크]] 를 겁니다. \"이번 세션 내용을 위키에 반영해 줘\" 처럼 지금 대화 "
            "내용을 기록해 달라는 요청이면, 먼저 이 대화에서 남길 만한 사실을 요약한 뒤 "
            "위 절차대로 기록하세요."
        ),
    },
    "long_prompt_brief": {
        "en": (
            "This is a long request. Before working on it, lay out JOB (what to do) / "
            "WHY (why) / GUARDRAILS (what not to do) / DONE MEANS (the finish line) in "
            "four lines and confirm with the user first. If this is an instruction for "
            "work already in progress, ignore this and continue."
        ),
        "ko": (
            "긴 요청입니다. 작업 전에 JOB(할 일) / WHY(이유) / GUARDRAILS(하면 안 되는 것) / "
            "DONE MEANS(끝의 기준) 네 줄로 정리해 사용자에게 먼저 확인받으세요. 이미 진행 중인 "
            "작업의 지시라면 무시하고 계속하세요."
        ),
    },
    "main_branch_guard": {
        "en": (
            "Do not edit directly on the {branch} branch. Create a working branch "
            "first: git switch -c <name>"
        ),
        "ko": (
            "{branch} 브랜치에서 직접 수정하지 않습니다. "
            "먼저 작업 브랜치를 만드세요: git switch -c <이름>"
        ),
    },
    "no_speculation": {
        "en": (
            "There is speculative wording: {matches}. Rewrite using only confirmed "
            "facts, or clearly mark anything you could not verify as \"could not "
            "verify\"."
        ),
        "ko": (
            "추측 표현이 있습니다: {matches}. 확인한 사실만으로 다시 쓰거나, "
            "확인하지 못한 부분은 '확인 못 함'이라고 분명히 적어 주세요."
        ),
    },
    "ask_after_wiki": {
        "en": (
            "Before asking, look for the answer in the wiki ({wiki_path}). Only ask "
            "if it is not in the wiki, and mention in one line where you looked."
        ),
        "ko": (
            "질문하기 전에 위키({wiki_path})에서 답을 찾아보세요. "
            "위키에 없을 때만 질문하고, 찾아본 곳을 한 줄로 밝혀 주세요."
        ),
    },
    "verify_before_done": {
        "en": (
            "A file was edited but no verification command (test, build, etc.) was "
            "run afterward. Verify it, note the result, and then say it is done."
        ),
        "ko": (
            "파일을 고친 뒤 확인 명령(테스트·빌드 등)을 실행하지 않았습니다. "
            "확인하고 결과를 적은 뒤 완료라고 해 주세요."
        ),
    },
}


def text(hook_id: str, lang: str, **kwargs) -> str:
    lang = lang if lang in ("en", "ko") else "en"
    table = TEXTS[hook_id]
    return table.get(lang, table["en"]).format(**kwargs)

# Shared with make_dist.sh's leak scan (plus sk-... for LLM API keys): the
# auto-update job file carries session text off the machine's normal wiki
# path into a scratch dir a background CLI reads, so it gets the same
# treatment, and the worker's stderr log line does too.
# Prefixes short/generic enough to appear mid-identifier in ordinary text
# (sk-, hf_, npm_, AIza, gh[oprsu]_) each carry a negative lookbehind so a
# preceding letter/digit blocks the match — otherwise "risk-based-approach",
# "mask-image-layer", "task-runner-config", "disk-usage-report", etc. would
# be false positives (their "...sk-..." / "...gh_..." substring plus enough
# following word characters matches the bare pattern).
SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9])gh[oprsu]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[0-9A-Za-z-]{10,}"
    r"|xapp-[0-9A-Za-z-]{10,}"
    r"|ATATT[0-9A-Za-z_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|sk_live_[A-Za-z0-9]{10,}"
    r"|rk_live_[A-Za-z0-9]{10,}"
    r"|(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{10,}"
    r"|(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{10,}"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{10,}"
    r"|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
    r"|[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"
    r"|(?<![A-Za-z0-9])npm_[A-Za-z0-9]{10,}"
    r"|(?<![A-Za-z0-9])hf_[A-Za-z0-9]{10,}"
)


def redact(text: str) -> str:
    return SECRET_RE.sub("[REDACTED]", text or "")


def log(kit_home: Path, line: str) -> None:
    """Append a timestamped line to the shared auto-update log, trimming it
    to its last 500 lines once it grows past 1000 (both wiki_auto_update.py
    and _auto_update_worker.py call this instead of keeping their own
    copies)."""
    log_path = Path(kit_home) / "state" / "auto-update.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {line}\n")
        _trim_log(log_path)
    except OSError:
        pass


def _trim_log(log_path: Path, max_lines: int = 1000, keep_lines: int = 500) -> None:
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return
    if len(lines) > max_lines:
        try:
            log_path.write_text("".join(lines[-keep_lines:]), encoding="utf-8")
        except OSError:
            pass


LEDGER_MAX_AGE_SECONDS = 30 * 24 * 3600


def load_ledger(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def ledger_count(ledger: dict, session_id: str) -> int:
    """The handled-message count for `session_id`, reading both the current
    `{count, ts}` entry shape and the older bare-int shape."""
    entry = ledger.get(session_id) if isinstance(ledger, dict) else None
    if isinstance(entry, dict):
        count = entry.get("count", 0)
        return count if isinstance(count, int) else 0
    if isinstance(entry, int):
        return entry
    return 0


def bump_ledger(ledger: dict, session_id: str, count: int) -> dict:
    """Records `count` for `session_id`, never letting it go down (a retry
    or an out-of-order write must not un-handle already-handled messages).
    A falsy `session_id` is skipped entirely — nothing worth tracking."""
    if not session_id:
        return ledger
    prev = ledger_count(ledger, session_id)
    ledger[session_id] = {"count": max(prev, count), "ts": datetime.now(timezone.utc).isoformat()}
    return ledger


def save_ledger(path: Path, data: dict) -> None:
    """Prunes entries older than 30 days, then writes via a tmp file +
    os.replace so a crash mid-write never leaves a truncated ledger."""
    now = time.time()
    pruned: dict = {}
    for session_id, entry in (data or {}).items():
        if not session_id:
            continue
        if isinstance(entry, int):
            entry = {"count": entry, "ts": datetime.now(timezone.utc).isoformat()}
        if not isinstance(entry, dict):
            continue
        ts = entry.get("ts")
        try:
            ts_val = datetime.fromisoformat(ts).timestamp() if ts else now
        except Exception:
            ts_val = now
        if now - ts_val > LEDGER_MAX_AGE_SECONDS:
            continue
        pruned[session_id] = entry
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(pruned), encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        pass


def _is_synthetic_user_text(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped.startswith(SYNTHETIC_USER_PREFIXES)


def kit_home() -> Path:
    """The installed `.keel/` directory: two levels up from this file
    (`.keel/hooks/_common.py` -> `.keel/`)."""
    return Path(__file__).resolve().parent.parent


# session_capture.py's raw-session-note headers, bilingual by `ui_language`
# (default English — see ui_lang()).
SESSION_CAPTURE_HEADERS = {
    "en": {"request": "## Request", "last_answer": "## Last answer"},
    "ko": {"request": "## 요청", "last_answer": "## 마지막 답"},
}


def session_capture_header(key: str, lang: str) -> str:
    table = SESSION_CAPTURE_HEADERS.get(lang, SESSION_CAPTURE_HEADERS["en"])
    return table[key]


def ui_lang(config: dict | None) -> str:
    """The language hook text should be written in: the page-chosen
    `ui_language` from config.json; if that key is missing entirely, the
    legacy `language` field (older config.json written before
    `ui_language` existed); English otherwise. An explicitly-set but
    unsupported `ui_language` (present, just not "en"/"ko") still falls
    back straight to English — only a *missing* key defers to `language`."""
    if not isinstance(config, dict):
        return "en"
    if "ui_language" in config and config["ui_language"] is not None:
        lang = config["ui_language"]
    else:
        lang = config.get("language")
    return lang if lang in ("en", "ko") else "en"


def load_config(kh: Path | None = None) -> dict | None:
    kh = kh if kh is not None else kit_home()
    try:
        text = (Path(kh) / "config.json").read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def read_payload() -> dict:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _strip_tool_prefix(name: str) -> str:
    for prefix in ("functions.", "tools."):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _apply_patch_text(tool_input) -> str | None:
    if isinstance(tool_input, str):
        return tool_input
    if isinstance(tool_input, dict):
        for key in ("command", "patch", "input"):
            val = tool_input.get(key)
            if isinstance(val, str) and "*** Begin Patch" in val:
                return val
    return None


def _parse_apply_patch(text: str, cwd: str) -> list[tuple[str, dict]]:
    results: list[tuple[str, dict]] = []
    for match in APPLY_PATCH_HEADER.finditer(text):
        kind, rel_path = match.group(1), match.group(2).strip()
        if kind == "Delete":
            continue
        abs_path = rel_path if os.path.isabs(rel_path) else str(Path(cwd) / rel_path)
        tool_name = "Write" if kind == "Add" else "Edit"
        results.append((tool_name, {"file_path": abs_path}))
    return results


def _flatten_shell_command(command):
    if isinstance(command, list):
        if len(command) >= 3 and command[-2] in ("-c", "-lc", "-ilc"):
            return command[-1]
        return shlex.join(command)
    return command


def normalize_tool(name: str, tool_input, cwd: str = ".") -> list[tuple[str, dict]]:
    if not isinstance(name, str):
        return [(name, tool_input or {})]
    stripped = _strip_tool_prefix(name)
    lower = stripped.lower()

    if lower in SHELL_TOOL_NAMES:
        ti = tool_input if isinstance(tool_input, dict) else {}
        command = ti.get("command")
        if command is None:
            command = ti.get("cmd", "")
        command = _flatten_shell_command(command)
        return [("Bash", {"command": command})]

    if lower in PATCH_TOOL_NAMES:
        patch_text = _apply_patch_text(tool_input)
        if patch_text:
            parsed = _parse_apply_patch(patch_text, cwd)
            if parsed:
                return parsed
        return [(stripped, tool_input or {})]

    return [(stripped, tool_input or {})]


def _filtered_user_text(content) -> str | None:
    """Text for a user message with synthetic parts dropped, or None if nothing real remains."""
    if isinstance(content, str):
        if _is_synthetic_user_text(content):
            return None
        return content
    if isinstance(content, list):
        pieces = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "input_text", "output_text"):
                t = part.get("text")
                if isinstance(t, str) and not _is_synthetic_user_text(t):
                    pieces.append(t)
        text = "".join(pieces)
        return text if text.strip() else None
    return None


def _text_from_content_parts(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "input_text", "output_text"):
                t = part.get("text")
                if isinstance(t, str):
                    pieces.append(t)
        return "".join(pieces)
    return ""


def _tools_from_content_parts(content) -> list[tuple[str, dict]]:
    tools: list[tuple[str, dict]] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                name = part.get("name", "")
                tool_input = part.get("input", {})
                tools.extend(normalize_tool(name, tool_input))
    return tools


def _parse_codex_arguments(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return raw
        return parsed if isinstance(parsed, dict) else raw
    return {}


def read_transcript_with_source(path: str) -> tuple[list[dict], str]:
    messages: list[dict] = []
    source = "claude"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return [], "claude"

    cwd = "."
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue

        row_type = row.get("type")
        row_payload = row.get("payload")
        row_payload = row_payload if isinstance(row_payload, dict) else None

        if row_payload is not None and row_payload.get("type") == "item_completed":
            source = "codex"
            item = row_payload.get("item")
            if isinstance(item, dict) and item.get("type") == "CommandExecution":
                command = _flatten_shell_command(item.get("command", ""))
                tools = [("Bash", {"command": command})]
                if messages and messages[-1]["role"] == "assistant":
                    messages[-1]["tools"].extend(tools)
                else:
                    messages.append({"role": "assistant", "text": "", "tools": tools})
            continue

        if row_type in ("user", "assistant"):
            message = row.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            role = message.get("role", row_type)
            if row_type == "user":
                # Skip tool_result-only user rows: no text content anywhere.
                if isinstance(content, list):
                    has_text = any(
                        isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text")
                        for p in content
                    )
                    if not has_text:
                        continue
                text = _filtered_user_text(content)
                if text is None:
                    continue
                messages.append({"role": role, "text": text, "tools": []})
            else:
                text = _text_from_content_parts(content)
                tools = _tools_from_content_parts(content)
                messages.append({"role": role, "text": text, "tools": tools})
            continue

        if row_type == "response_item":
            source = "codex"
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            ptype = payload.get("type")
            if ptype == "message":
                role = payload.get("role", "")
                pcontent = payload.get("content")
                if role == "user":
                    text = _filtered_user_text(pcontent)
                    if text is None:
                        continue
                else:
                    text = _text_from_content_parts(pcontent)
                messages.append({"role": role, "text": text, "tools": []})
            elif ptype in ("function_call", "custom_tool_call"):
                name = payload.get("name", "")
                args = _parse_codex_arguments(payload.get("arguments") or payload.get("input"))
                tools = normalize_tool(name, args, cwd)
                if messages and messages[-1]["role"] == "assistant":
                    messages[-1]["tools"].extend(tools)
                else:
                    messages.append({"role": "assistant", "text": "", "tools": tools})
            continue

        if row_type in ("session_meta", "turn_context"):
            source = "codex"
            if row_payload is not None:
                cwd = row_payload.get("cwd", cwd)
            continue

    return messages, source


def read_transcript(path: str) -> list[dict]:
    messages, _source = read_transcript_with_source(path)
    return messages


def last_turn(msgs: list[dict]) -> list[dict]:
    last_user_index = -1
    for i, m in enumerate(msgs):
        if m.get("role") == "user" and (m.get("text") or "").strip():
            last_user_index = i
    return msgs[last_user_index + 1:]


def last_assistant_text(msgs) -> str:
    for m in reversed(msgs):
        if m.get("role") == "assistant" and (m.get("text") or "").strip():
            return m["text"]
    return ""


def emit_context(event: str, text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def block_stop(reason: str) -> int:
    reason = KIT_PREFIX + reason
    print(json.dumps({"decision": "block", "reason": reason}))
    print(reason, file=sys.stderr)
    return 2


def deny_tool(reason: str) -> int:
    reason = KIT_PREFIX + reason
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    print(reason, file=sys.stderr)
    return 2


def may_block_stop(payload: dict, hook_id: str, kh: Path | None = None) -> bool:
    if payload.get("stop_hook_active"):
        return False
    kh = kh if kh is not None else kit_home()
    state_dir = Path(kh) / "state"

    session_id = payload.get("session_id", "")
    transcript_path = payload.get("transcript_path", "")
    msgs = read_transcript(transcript_path) if transcript_path else []
    last_user_text = ""
    for m in reversed(msgs):
        if m.get("role") == "user" and (m.get("text") or "").strip():
            last_user_text = m["text"]
            break
    if not last_user_text:
        last_user_text = payload.get("turn_id", "")

    if last_user_text.strip().startswith(KIT_PREFIX.strip()):
        return False

    digest = hashlib.sha1(last_user_text.encode("utf-8", "ignore")).hexdigest()[:12]
    marker_name = "stop-%s-%s-%s" % (hook_id, session_id, digest)
    marker_path = state_dir / marker_name

    if marker_path.exists():
        return False

    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for f in state_dir.glob("stop-*"):
            try:
                if now - f.stat().st_mtime > 86400:
                    f.unlink()
            except OSError:
                pass
        marker_path.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass

    return True


def is_nested() -> bool:
    """True when this hook is running inside a CLI invocation the kit itself
    started (e.g. the background wiki-auto-update worker's `claude`/`codex`
    run) — those runs set LLM_WIKI_KIT_NESTED=1 so their own hook firings are
    no-ops instead of recursing or double-writing state."""
    return os.environ.get("LLM_WIKI_KIT_NESTED") == "1"


def main_guard(fn):
    def wrapper(*args, **kwargs):
        if os.environ.get("LLM_WIKI_KIT_SUPPRESS") == "1":
            return 0
        if is_nested():
            return 0
        try:
            result = fn(*args, **kwargs)
        except Exception:
            return 0
        return result if isinstance(result, int) else 0
    return wrapper
