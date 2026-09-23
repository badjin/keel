"""Obsidian detection and minimal per-wiki config."""
from __future__ import annotations
import json
import urllib.parse
from pathlib import Path

CORE_PLUGINS = [
    "file-explorer",
    "global-search",
    "switcher",
    "graph",
    "backlink",
    "outgoing-link",
    "tag-pane",
    "page-preview",
]

GRAPH_CONFIG = {"showTags": False, "showAttachments": False, "hideUnresolved": False}

GLOBAL_CANDIDATES = [Path("/Applications/Obsidian.app")]


def find_obsidian(home: Path) -> str | None:
    home = Path(home)
    candidates = list(GLOBAL_CANDIDATES) + [home / "Applications" / "Obsidian.app"]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def install_hint() -> dict:
    return {
        "brew": "brew install --cask obsidian",
        "url": "https://obsidian.md/download",
    }


def _write_if_missing(path: Path, data, created: list) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    created.append(path)


def write_obsidian_config(wiki_path: Path) -> list:
    wiki_path = Path(wiki_path)
    obsidian_dir = wiki_path / ".obsidian"
    created: list = []
    _write_if_missing(obsidian_dir / "app.json", {}, created)
    _write_if_missing(obsidian_dir / "core-plugins.json", CORE_PLUGINS, created)
    _write_if_missing(obsidian_dir / "graph.json", GRAPH_CONFIG, created)
    return created


def open_url(wiki_path: Path) -> str:
    return "obsidian://open?path=" + urllib.parse.quote(str(wiki_path))
