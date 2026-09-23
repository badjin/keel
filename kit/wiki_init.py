"""Creates the wiki skeleton and writes the kit's config.json."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

from kit.errors import KitValueError

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

GITIGNORE = ".obsidian/workspace*.json\n.DS_Store\n"


def write_config(home: Path, wiki_path: Path, language: str, ui_language: str | None = None) -> Path:
    home = Path(home)
    wiki_path = Path(wiki_path)
    config_path = home / ".keel" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.update({"wiki_path": str(wiki_path), "language": language})
    if ui_language is not None:
        data["ui_language"] = ui_language if ui_language in ("en", "ko") else "en"
    config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_path


def _is_under(home: Path, wiki_path: Path) -> bool:
    home_resolved = home.resolve()
    try:
        wiki_resolved = wiki_path.resolve()
    except Exception:
        wiki_resolved = wiki_path
    if wiki_resolved == home_resolved:
        return True
    try:
        wiki_resolved.relative_to(home_resolved)
        return True
    except ValueError:
        return False


def _write_if_missing(path: Path, text: str, created: list) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    created.append(path)


def init_wiki(
    home: Path,
    wiki_path: Path,
    language: str,
    git_init: bool = True,
    ui_language: str | None = None,
) -> list:
    home = Path(home)
    wiki_path = Path(wiki_path)
    if not _is_under(home, wiki_path):
        raise KitValueError("wiki_path_outside_home", wiki_path=wiki_path, home=home)

    wiki_path.mkdir(parents=True, exist_ok=True)

    lang = language if language in ("ko", "en") else "ko"
    index_template = (TEMPLATES_DIR / f"index.{lang}.md").read_text(encoding="utf-8")
    schema_template = (TEMPLATES_DIR / f"schema.{lang}.md").read_text(encoding="utf-8")

    created: list = []
    _write_if_missing(wiki_path / "index.md", index_template, created)
    _write_if_missing(wiki_path / "schema.md", schema_template, created)
    _write_if_missing(wiki_path / "raw" / "sessions" / ".gitkeep", "", created)
    _write_if_missing(wiki_path / "wiki" / ".gitkeep", "", created)
    _write_if_missing(wiki_path / "repos" / ".gitkeep", "", created)
    _write_if_missing(wiki_path / "local" / ".gitkeep", "", created)
    _write_if_missing(wiki_path / ".gitignore", GITIGNORE, created)

    if git_init and not (wiki_path / ".git").exists():
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(wiki_path),
            capture_output=True,
            check=False,
        )

    write_config(home, wiki_path, lang, ui_language)

    return created


def add_index_links(wiki_path: Path, section: str, links: list) -> None:
    wiki_path = Path(wiki_path)
    index_path = wiki_path / "index.md"
    text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    header = f"## {section}"
    has_header = any(line.strip() == header for line in text.split("\n"))
    if not has_header:
        if text and not text.endswith("\n"):
            text += "\n"
        if text and not text.endswith("\n\n"):
            text += "\n"
        text += f"{header}\n"

    lines = text.split("\n")
    header_idx = next(i for i, line in enumerate(lines) if line.strip() == header)

    section_end = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            section_end = i
            break

    existing = set(lines[header_idx + 1:section_end])
    to_add = [f"- [[{link}]]" for link in links if f"- [[{link}]]" not in existing]

    if to_add:
        insert_at = section_end
        while insert_at > header_idx + 1 and lines[insert_at - 1].strip() == "":
            insert_at -= 1
        lines[insert_at:insert_at] = to_add

    index_path.write_text("\n".join(lines), encoding="utf-8")
