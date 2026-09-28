from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from . import docs, fsutil


SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")


def work_root(kb: Path) -> Path:
    return kb / "raw" / "work"


def new_work(kb: Path, keel_home: Path, slug: str, lang: str, origin: str, today: str) -> Path:
    if not SLUG.fullmatch(slug):
        raise ValueError(slug)
    folder = work_root(kb) / f"{today}-{slug}"
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "records").mkdir()
    templates = keel_home / "workflow" / "templates"
    template = templates / f"intent.{lang}.md"
    if not template.is_file():
        template = templates / "intent.en.md"
    intent = template.read_text(encoding="utf-8").format(title=slug, date=today)
    fsutil.atomic_write_text(folder / "intent.md", intent)
    fsutil.atomic_write_json(folder / "state.json", {
        "id": folder.name,
        "slug": slug,
        "origin": origin,
        "lang": lang,
        "created": today,
        "sessions": [],
        "reviews": {},
        "review_fails": {},
    })
    return folder


def resolve(kb: Path, name_or_path: str) -> Path:
    path = Path(name_or_path)
    if (path.is_absolute() or "/" in name_or_path or "\\" in name_or_path) and path.is_dir():
        return path
    root = work_root(kb)
    exact = root / name_or_path
    if exact.is_dir() and exact.resolve().parent == root.resolve():
        return exact
    candidates = sorted(folder for folder in root.iterdir() if folder.is_dir()) if root.is_dir() else []
    matches = [folder for folder in candidates
               if re.sub(r"^\d{4}-\d{2}-\d{2}-", "", folder.name) == name_or_path]
    if len(matches) == 1:
        return matches[0]
    raise LookupError(name_or_path, matches or candidates)


def load_state(folder: Path) -> dict:
    return fsutil.read_json(folder / "state.json", {})


def update_state(folder: Path, fn: Callable[[dict], object]) -> dict:
    return fsutil.update_json(folder / "state.json", fn)


def add_session(folder: Path, session: str, cli: str, transcript_path: str) -> bool:
    added = False

    def append_once(state: dict) -> None:
        nonlocal added
        sessions = state["sessions"]
        if any(item["session"] == session for item in sessions):
            return
        sessions.append({
            "session": session,
            "cli": cli,
            "transcript_path": transcript_path,
            "first_seen": fsutil.now_iso(),
        })
        added = True

    update_state(folder, append_once)
    return added


def open_items(kb: Path) -> list[Path]:
    root = work_root(kb)
    if not root.is_dir():
        return []
    return sorted(folder for folder in root.iterdir()
                  if folder.is_dir() and (folder / "intent.md").is_file()
                  and docs.get_status((folder / "intent.md").read_text(encoding="utf-8")) != "done")
