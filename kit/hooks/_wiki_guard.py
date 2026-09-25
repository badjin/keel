"""Guard for the session-end KB update.

The CLI edits a temporary copy of the KB's Markdown files; check_update()
decides whether the copy kept every existing line; apply_changes() writes
the accepted files back only if the live KB still holds what was copied.
Same rules as the server wiki monitor's validate().
"""
from __future__ import annotations
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FOOTER = re.compile(rb"## Last Updated: [0-9]{4}-[0-9]{2}-[0-9]{2}(?:-[A-Za-z0-9]+)?(.*)")
NOTICE_FILE = "auto-update-notice.json"


def read_tree(root: Path, md_only: bool = False) -> dict:
    """{posix relative path: bytes} for every regular file under root,
    skipping .git folders and symlinks (never followed)."""
    result = {}
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d != ".git" and not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or (md_only and not name.endswith(".md")):
                continue
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, "rb") as fh:
                result[rel] = fh.read()
    return result


def make_stage(wiki: Path) -> tuple:
    """Copies the KB's Markdown files into a new temp folder. Returns
    (stage path, snapshot of what was copied). The path is resolved so a
    CLI comparing real paths (/private/var/... on macOS) sees its own cwd."""
    before = read_tree(wiki, md_only=True)
    stage = Path(tempfile.mkdtemp(prefix="keel-update-")).resolve()
    try:
        for rel, data in before.items():
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return stage, before


def changed_paths(before: dict, after: dict) -> list:
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def _allowed(rel: str) -> bool:
    return rel == "index.md" or (rel.startswith("wiki/") and rel.endswith(".md"))


def _footer_count(data: bytes) -> int:
    return sum(1 for line in data.splitlines() if FOOTER.fullmatch(line))


def _footer_note(line: bytes):
    """The text after a footer line's date, or None when line is not a footer."""
    match = FOOTER.fullmatch(line)
    return match.group(1) if match is not None else None


def _keeps_lines(old: bytes, new: bytes) -> bool:
    """Every line of old appears in new in the same order. A footer line
    may change its date but must keep the text after the date."""
    remaining = iter(new.splitlines())
    for line in old.splitlines():
        match = FOOTER.fullmatch(line)
        if match:
            note = match.group(1).strip()
            found = False
            for candidate in remaining:
                candidate_note = _footer_note(candidate)
                if candidate_note is not None and note in candidate_note:
                    found = True
                    break
        else:
            found = any(c == line for c in remaining)
        if not found:
            return False
    return True


def check_update(before: dict, after: dict) -> list:
    """[(problem, path), ...] — empty when the update may be applied.
    problem is one of: deleted, outside, changed, footer."""
    problems = []
    for rel in changed_paths(before, after):
        if rel not in after:
            problems.append(("deleted", rel))
        elif not _allowed(rel):
            problems.append(("outside", rel))
        elif rel in before and not _keeps_lines(before[rel], after[rel]):
            problems.append(("changed", rel))
        elif _footer_count(after[rel]) > max(1, _footer_count(before.get(rel, b""))):
            problems.append(("footer", rel))
    return problems


def _has_symlink(wiki: Path, rel: str) -> bool:
    path = Path(wiki)
    for part in rel.split("/"):
        path = path / part
        if path.is_symlink():
            return True
    return False


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".keel-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_changes(wiki: Path, before: dict, after: dict, changed: list) -> bool:
    """Writes after[rel] for each changed path. Returns False (and writes
    nothing) if any live file differs from before[rel] (or exists when it
    was absent), or if any path component below the wiki root is a
    symlink. A failure part-way restores the files already written."""
    wiki = Path(wiki)
    for rel in changed:
        if _has_symlink(wiki, rel):
            return False
        live = wiki / rel
        current = live.read_bytes() if live.exists() else None
        if current != before.get(rel):
            return False
    written = []
    try:
        for rel in changed:
            _atomic_write(wiki / rel, after[rel])
            written.append(rel)
    except BaseException:
        for rel in written:
            if rel in before:
                _atomic_write(wiki / rel, before[rel])
            else:
                (wiki / rel).unlink(missing_ok=True)
        raise
    return True


def record_notice(kit_home: Path, kind: str, problem: str = "", path: str = "") -> None:
    """kind: "rejected" or "conflict". A later notice replaces an earlier
    unshown one and carries the running count."""
    notice_path = Path(kit_home) / "state" / NOTICE_FILE
    try:
        count = int(json.loads(notice_path.read_text(encoding="utf-8")).get("count", 0))
    except Exception:
        count = 0
    notice = {"at": datetime.now(timezone.utc).isoformat(), "kind": kind,
              "problem": problem, "path": path, "count": count + 1}
    tmp = notice_path.with_name(NOTICE_FILE + ".tmp")
    try:
        notice_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(notice), encoding="utf-8")
        os.replace(tmp, notice_path)
    except OSError:
        pass


def take_notice(kit_home: Path):
    """Returns the pending notice dict and removes it, or None."""
    notice_path = Path(kit_home) / "state" / NOTICE_FILE
    taken = notice_path.with_name(f"{NOTICE_FILE}.{os.getpid()}.taken")
    try:
        os.replace(notice_path, taken)
    except OSError:
        return None
    try:
        data = json.loads(taken.read_text(encoding="utf-8"))
    except Exception:
        data = None
    try:
        taken.unlink()
    except OSError:
        pass
    return data if isinstance(data, dict) else None
