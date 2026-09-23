"""Builds wiki pages that index local folders on disk."""
from __future__ import annotations
import os
import re
import subprocess
from pathlib import Path

from kit.wiki_init import add_index_links
from kit.errors import KitValueError

SKIP_NAMES = {"node_modules", "__pycache__", "venv", "dist", "build"}
MAX_TREE_LINES = 200
README_NAMES = ("README.md", "README", "readme.md")

LABELS = {
    "ko": {
        "path": "경로",
        "tree": "구조",
        "readme": "README",
        "git_subfolders": "git 저장소 폴더",
        "git_note": "이 폴더들은 GitHub 탭의 히스토리 파이프라인에서 따로 색인할 수 있습니다.",
    },
    "en": {
        "path": "Path",
        "tree": "Tree",
        "readme": "README",
        "git_subfolders": "Git subfolders",
        "git_note": "These folders can be indexed from the GitHub tab's history pipeline.",
    },
}


def safe_under(home: Path, p: Path) -> Path:
    home_resolved = Path(home).resolve()
    try:
        p_resolved = Path(p).resolve()
    except Exception as exc:
        raise KitValueError("path_unresolvable", p=p) from exc
    if p_resolved == home_resolved:
        return p_resolved
    try:
        p_resolved.relative_to(home_resolved)
    except ValueError:
        raise KitValueError("path_outside_home", p=p, home=home) from None
    return p_resolved


def list_dirs(home: Path, p: Path) -> list:
    base = safe_under(home, p)
    result = []
    if not base.is_dir():
        return result
    try:
        children = list(base.iterdir())
    except OSError:
        return result
    for child in children:
        try:
            if child.is_symlink():
                continue
            if not child.is_dir():
                continue
        except OSError:
            continue
        if child.name.startswith("."):
            continue
        try:
            is_git = (child / ".git").exists()
        except OSError:
            is_git = False
        result.append(
            {
                "name": child.name,
                "path": str(child),
                "is_git": is_git,
            }
        )
    result.sort(key=lambda d: d["name"])
    return result


def git_branches(path: Path) -> dict:
    """Local branches of the git repo at `path`, plus a default branch guess:
    "main" if present, else "master" if present, else the current branch."""
    try:
        is_git = (path / ".git").exists()
    except OSError:
        is_git = False
    if not is_git:
        raise KitValueError("not_git_repo", path=path)

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"

    result = subprocess.run(
        ["git", "-C", str(path), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if stderr:
            raise ValueError(stderr)
        raise KitValueError("branch_check_failed")
    branches = [line for line in result.stdout.splitlines() if line.strip()]

    head_result = subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    current = head_result.stdout.strip()

    if "main" in branches:
        default = "main"
    elif "master" in branches:
        default = "master"
    else:
        default = current

    return {"branches": branches, "default": default}


def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^\w-]+", "-", slug)
    slug = slug.strip("-")
    return slug or "folder"


def _build_tree_lines(folder: Path, max_depth: int = 2) -> list:
    all_lines: list = []

    def walk(dir_path: Path, depth: int, prefix: str):
        if depth > max_depth:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or entry.name in SKIP_NAMES:
                continue
            try:
                is_symlink = entry.is_symlink()
            except OSError:
                is_symlink = False
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            if is_symlink and is_dir:
                continue
            label = entry.name + ("/" if is_dir else "")
            all_lines.append(f"{prefix}{label}")
            if is_dir:
                walk(entry, depth + 1, prefix + "  ")

    walk(folder, 1, "")

    if len(all_lines) > MAX_TREE_LINES:
        remaining = len(all_lines) - MAX_TREE_LINES
        lines = all_lines[:MAX_TREE_LINES]
        lines.append(f"… ({remaining} more)")
        return lines
    return all_lines


def _find_readme(folder: Path):
    for name in README_NAMES:
        candidate = folder / name
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _find_git_subfolders(folder: Path, max_depth: int = 2) -> list:
    result = []

    def walk(dir_path: Path, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if not is_dir or entry.name.startswith(".") or entry.name in SKIP_NAMES:
                continue
            try:
                if entry.is_symlink():
                    continue
            except OSError:
                continue
            try:
                is_git = (entry / ".git").exists()
            except OSError:
                continue
            if is_git:
                result.append(entry)
            else:
                walk(entry, depth + 1)

    walk(folder, 1)
    return result


def _marker(folder: Path) -> str:
    return f"<!-- local: {folder} -->"


def _resolve_local_slug(wiki_path: Path, folder: Path, used_slugs: set) -> str:
    base_slug = _slugify(folder.name)
    marker = _marker(folder)
    slug = base_slug
    n = 2
    while True:
        if slug not in used_slugs:
            existing_index = wiki_path / "local" / slug / "index.md"
            if not existing_index.exists():
                return slug
            text = existing_index.read_text(encoding="utf-8", errors="replace")
            if text.startswith("\ufeff"):
                text = text[1:]
            lines = text.splitlines()[:10]
            if marker in lines:
                return slug
        slug = f"{base_slug}-{n}"
        n += 1


def build_local_index(home: Path, wiki_path: Path, folders: list, language: str) -> list:
    home = Path(home)
    wiki_path = Path(wiki_path)
    lang = language if language in ("ko", "en") else "ko"
    labels = LABELS[lang]

    created: list = []
    used_slugs = set()
    seen_folders: set = set()

    for folder in folders:
        folder = safe_under(home, folder)
        if folder in seen_folders:
            continue
        seen_folders.add(folder)

        slug = _resolve_local_slug(wiki_path, folder, used_slugs)
        used_slugs.add(slug)

        page_dir = wiki_path / "local" / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        page_path = page_dir / "index.md"

        marker = _marker(folder)
        lines = [marker, f"# {folder.name}", "", f"{labels['path']}: {folder}", ""]

        lines.append(f"## {labels['tree']}")
        lines.append("")
        lines.append("```")
        lines.extend(_build_tree_lines(folder))
        lines.append("```")
        lines.append("")

        readme = _find_readme(folder)
        if readme is not None:
            try:
                readme_lines = readme.read_text(encoding="utf-8", errors="replace").splitlines()[:20]
            except Exception:
                readme_lines = []
            if readme_lines:
                lines.append(f"## {labels['readme']}")
                lines.append("")
                lines.append("> " + "\n> ".join(readme_lines))
                lines.append("")

        git_subfolders = _find_git_subfolders(folder)
        try:
            folder_is_git = (folder / ".git").exists()
        except OSError:
            folder_is_git = False
        if folder_is_git:
            git_subfolders = [folder] + git_subfolders
        if git_subfolders:
            lines.append(f"## {labels['git_subfolders']}")
            lines.append("")
            lines.append(labels["git_note"])
            lines.append("")
            for sub in git_subfolders:
                rel = sub.relative_to(folder)
                label = folder.name if str(rel) == "." else str(rel)
                lines.append(f"- {label}")
            lines.append("")

        page_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        created.append(page_path)

        add_index_links(
            wiki_path,
            "로컬 폴더" if lang == "ko" else "Local folders",
            [f"local/{slug}/index"],
        )

    return created
