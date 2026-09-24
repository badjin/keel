#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

MIN_USER_MESSAGES = 2
MIN_USER_CHARS = 200
MAX_USER_REQUESTS_CHARS = 30000


def _is_excluded_user_text(text: str) -> bool:
    """Rows the kit itself injected (its own Stop-hook nudges or additional
    context) are not real user speech — never count them toward the
    min-messages/min-chars filter and never copy them into the job."""
    stripped = (text or "").strip()
    return (
        stripped.startswith(_common.KIT_PREFIX.strip())
        or stripped.startswith(_common.STOP_HOOK_FEEDBACK_PREFIX)
    )


def _cap_user_requests(text: str) -> str:
    if len(text) <= MAX_USER_REQUESTS_CHARS:
        return text
    return text[-MAX_USER_REQUESTS_CHARS:]


def _git_top_level(cwd: str) -> str | None:
    if not cwd:
        return None
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=1,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    return top or None


def run() -> int:
    payload = _common.read_payload()
    kit_home = _common.kit_home()

    # Read the transcript before any early return below, so the source CLI
    # is known for the 30-day health-check rule even on a session that
    # itself turns out too short/uninteresting to enqueue its own job.
    transcript_path = payload.get("transcript_path", "")
    if transcript_path:
        msgs, source = _common.read_transcript_with_source(transcript_path)
    else:
        msgs, source = [], "claude"

    try:
        import _maintenance
        _maintenance.check_health(kit_home, source)
    except Exception:
        pass

    config = _common.load_config(kit_home)
    if config is None:
        return 0
    wiki_path = config.get("wiki_path", "")
    if not wiki_path:
        return 0

    session_id = payload.get("session_id", "") or ""

    user_texts = [
        m["text"].strip() for m in msgs
        if m.get("role") == "user" and (m.get("text") or "").strip()
        and not _is_excluded_user_text(m["text"])
    ]

    state_dir = kit_home / "state"
    ledger_path = state_dir / "auto-update-ledger.json"
    ledger = _common.load_ledger(ledger_path)
    handled_before = _common.ledger_count(ledger, session_id) if session_id else 0
    new_user_texts = user_texts[handled_before:]

    if not new_user_texts:
        _common.log(kit_home, f"session={session_id} skip no-new")
        return 0

    total_chars = sum(len(t) for t in new_user_texts)
    if len(new_user_texts) < MIN_USER_MESSAGES or total_chars < MIN_USER_CHARS:
        _common.log(kit_home, f"session={session_id} skip short")
        return 0

    cli_exe = shutil.which(source)
    if not cli_exe:
        _common.log(kit_home, f"session={session_id} skip no-cli")
        return 0

    cwd = payload.get("cwd", "")
    git_root = _git_top_level(cwd)
    last_text = _common.last_assistant_text(msgs)

    joined = "\n---\n".join(new_user_texts)

    # Order matters: neutralise a forged delimiter, then redact secrets,
    # then cap/truncate — redacting after truncation could leave a secret
    # the cap cut mid-way only partially masked instead of fully removed.
    user_requests = _common.neutralize_delimiters(joined)
    user_requests = _common.redact(user_requests)
    user_requests = _cap_user_requests(user_requests)

    last_assistant = _common.neutralize_delimiters(last_text)
    last_assistant = _common.redact(last_assistant)
    last_assistant = last_assistant[:2000]

    job = {
        "session_id": session_id,
        "cwd": cwd,
        "git_root": git_root,
        "cli": source,
        "wiki_path": wiki_path,
        "user_requests": user_requests,
        "user_message_count": len(new_user_texts),
        "handled_before": handled_before,
        "last_assistant": last_assistant,
        "created": datetime.now(timezone.utc).isoformat(),
    }

    queue_dir = state_dir / "auto-update"
    try:
        queue_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0

    sid8 = session_id[:8] if session_id else "nosid"
    job_path = queue_dir / f"{sid8}-{uuid.uuid4().hex[:8]}.json"
    tmp_path = job_path.with_suffix(job_path.suffix + ".tmp")
    try:
        fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(job, ensure_ascii=False, indent=2))
        os.replace(tmp_path, job_path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return 0

    worker = Path(__file__).resolve().parent / "_auto_update_worker.py"
    try:
        subprocess.Popen(
            [sys.executable, str(worker)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass

    return 0


main = _common.main_guard(run)

if __name__ == "__main__":
    sys.exit(main())
