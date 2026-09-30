from __future__ import annotations

import json
import re
from pathlib import Path

from . import docs, fsutil, work


def ledger_path(keel_home: Path) -> Path:
    return keel_home / "state" / "workflow" / "approvals.jsonl"


def match_prompt(prompt: str, words: list[str]) -> str | None:
    if not words:
        return None
    commands = "|".join(re.escape(word) for word in words)
    match = re.fullmatch(rf"(?:{commands})\s+([A-Za-z0-9-]+)", prompt.strip(), re.IGNORECASE)
    return match.group(1) if match else None


def approve(kb: Path, keel_home: Path, name: str, session: str, prompt: str, *, via: str = "typed message") -> tuple[str, str]:
    root = work.work_root(kb)
    candidates = []
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir() or not (folder.name == name or re.sub(r"^\d{4}-\d{2}-\d{2}-", "", folder.name) == name):
                continue
            intent_path = folder / "intent.md"
            if not intent_path.is_file():
                continue
            status = docs.get_status(intent_path.read_text(encoding="utf-8"))
            if status == "awaiting-approval" or (status == "approved" and approved_hash(keel_home, folder) is None):
                candidates.append(folder)
    if not candidates:
        return "none", name
    if len(candidates) > 1:
        return "ambiguous", name

    folder = candidates[0]
    intent_path = folder / "intent.md"
    original = intent_path.read_text(encoding="utf-8")
    digest = docs.intent_hash(original)
    state = work.load_state(folder)
    reviewed = state.get("reviews", {}).get("intent", {})
    if (reviewed.get("verdict") != "PASS" or reviewed.get("hashes", {}).get("intent") != digest
            or docs.check_intent(original, docs.text("none_tokens", state.get("lang", "en")))):
        return "stale", folder.name
    at = fsutil.now_iso()
    record = {
        "id": folder.name,
        "folder": str(folder.resolve()),
        "hash": digest,
        "at": at,
        "session": session,
        "prompt": prompt,
    }
    fsutil.append_line(ledger_path(keel_home), json.dumps(record, ensure_ascii=False))
    updated = docs.set_status(original, "approved")
    updated = docs.add_changelog(updated, f"{at[:10]} approved by {via} (hash {digest[:12]})")
    fsutil.atomic_write_text(intent_path, updated)
    return "approved", folder.name


def approved_hash(keel_home: Path, folder: Path) -> str | None:
    path = ledger_path(keel_home)
    if not path.is_file():
        return None
    current = docs.intent_hash((folder / "intent.md").read_text(encoding="utf-8"))
    resolved = str(folder.resolve())
    latest = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(record, dict) and record.get("folder") == resolved and record.get("hash") == current:
            latest = current
    return latest


def is_approved(keel_home: Path, folder: Path) -> bool:
    return approved_hash(keel_home, folder) is not None
