from __future__ import annotations

import json
from pathlib import Path

from . import fsutil


def _resolve(value, paths: dict):
    if isinstance(value, str):
        for name, path in paths.items():
            if isinstance(path, dict):
                if "{" + name + "}" in value:
                    value = " or ".join(
                        value.replace("{" + name + "}", str(path[target]))
                        .replace("CLAUDE.md or AGENTS.md", "CLAUDE.md" if target == "claude" else "AGENTS.md")
                        for target in ("claude", "codex") if target in path
                    )
            else:
                value = value.replace("{" + name + "}", str(path))
        return value
    if isinstance(value, list):
        return [_resolve(item, paths) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, paths) for key, item in value.items()}
    return value


def _script_json(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def render(map_data: dict, manifest: dict, catalogue: list,
           lang: str, paths: dict, embed: bool = False) -> str:
    template = (Path(__file__).parent / "map_template.html").read_text(encoding="utf-8")
    resolved_map = _resolve(map_data, paths)
    resolved_map["catalogue"] = [
        {**item, "path": str(Path(paths["KEEL_HOME"]) / "hooks" / item["script"])}
        for item in catalogue
    ]
    resolved_manifest = _resolve(manifest, paths)
    return (template.replace("/*__KEEL_MAP_DATA__*/", "const MAP = " + _script_json(resolved_map) + ";")
            .replace("/*__KEEL_MANIFEST__*/", _script_json(resolved_manifest))
            .replace("__KEEL_VERSION__", str(manifest["version"]))
            .replace("__KEEL_LANG__", lang if lang in ("en", "ko") else "en")
            .replace("__KEEL_EMBED__", "1" if embed else "0"))


def write_map(home: Path, repo_root: Path) -> Path:
    home = Path(home).resolve()
    repo_root = Path(repo_root).resolve()
    workflow = repo_root / "kit" / "workflow"
    catalogue_path = repo_root / "kit" / "catalogue.json"
    config = fsutil.read_json(home / ".keel" / "config.json", {})
    kb = Path(config["wiki_path"]).expanduser().resolve()
    map_data = json.loads((workflow / "map.json").read_text(encoding="utf-8"))
    manifest = json.loads((workflow / "manifest.json").read_text(encoding="utf-8"))
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    paths = {"KEEL_HOME": str(home / ".keel"), "KB_PATH": str(kb),
             "CLI_HOME": {target: str(home / f".{target}") for target in ("claude", "codex")}}
    html = render(map_data, manifest, catalogue, config.get("ui_language", "en"), paths)
    destination = kb / "keel-harness-map.html"
    fsutil.atomic_write_text(destination, html)
    return destination
