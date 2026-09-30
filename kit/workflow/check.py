from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import fsutil, rules_block


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_status(path: Path, expected: str | None) -> str:
    if not path.is_file():
        return "missing"
    return "installed" if expected and _sha(path) == expected else "different"


def _rules_status(path: Path, expected: str | None) -> str:
    if not path.is_file():
        return "missing"
    content = path.read_text(encoding="utf-8")
    if not rules_block.START_RE.search(content):
        return "missing"
    try:
        bounds = rules_block._bounds(content)
    except rules_block.BlockError:
        return "different"
    block = content[bounds[0]:bounds[1]]
    return "installed" if expected and hashlib.sha256(block.encode("utf-8")).hexdigest() == expected else "different"


def _hook_status(settings_path: Path, command: str | None) -> str:
    settings = fsutil.read_json(settings_path, {})
    for groups in settings.get("hooks", {}).values():
        for group in groups:
            if any(hook.get("command") == command for hook in group.get("hooks", [])):
                return "installed"
    return "missing"


def _installed_hook_ids(home: Path, catalogue: list[dict]) -> dict[str, set[str]]:
    result = {"claude": set(), "codex": set()}
    for target, filename in (("claude", "settings.json"), ("codex", "hooks.json")):
        settings = fsutil.read_json(home / f".{target}" / filename, {})
        commands = (hook.get("command", "") for groups in settings.get("hooks", {}).values()
                    for group in groups for hook in group.get("hooks", []))
        for command in commands:
            for item in catalogue:
                if f'/.keel/hooks/{item["script"]}' in command:
                    result[target].add(item["id"])
    return result


def run_check(keel_home: Path) -> tuple[list[dict], int]:
    keel_home = Path(keel_home).resolve()
    home = keel_home.parent
    workflow = keel_home / "workflow"
    config = fsutil.read_json(keel_home / "config.json", {})
    installed = bool(config.get("workflow", {}).get("installed"))
    metadata = fsutil.read_json(keel_home / "state/workflow/installed.json", {})
    source = workflow if (workflow / "manifest.json").exists() else Path(__file__).parent
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    map_data = json.loads((source / "map.json").read_text(encoding="utf-8"))
    catalogue_path = workflow / "catalogue.json"
    if not catalogue_path.exists():
        catalogue_path = Path(__file__).parent.parent / "catalogue.json"
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    selected = config.get("workflow", {}).get("targets", []) if installed else ["claude", "codex"]
    kb = Path(config["wiki_path"]).expanduser().resolve() if config.get("wiki_path") else keel_home
    rows = []

    for item in manifest["items"]:
        item_id = item["id"]
        targets = [target for target in selected if target in item["targets"]] if (
            "{CLI_HOME}" in item["path"] or item["kind"] == "hook") else [None]
        for target in targets:
            cli_home = home / f".{target}" if target else None
            path_text = item["path"].replace("{KEEL_HOME}", str(keel_home)).replace("{KB_PATH}", str(kb))
            if cli_home:
                path_text = path_text.replace("{CLI_HOME}", str(cli_home))
                path_text = path_text.replace("CLAUDE.md or AGENTS.md",
                                              "CLAUDE.md" if target == "claude" else "AGENTS.md")
            path = Path(path_text)
            if not installed:
                status = "missing"
            elif item_id in ("wf:map", "wf:ladder"):
                status = "installed" if path.is_file() else "missing"
            elif item["kind"] == "rules":
                status = _rules_status(path, metadata.get("rules", {}).get(str(path), {}).get("sha256"))
            elif item["kind"] == "hook":
                commands = metadata.get("hooks", {}).get(target, [])
                script = item["script"]
                command = next((value for value in commands if f'/workflow-hooks/{script}"' in value), None)
                settings_path = cli_home / ("settings.json" if target == "claude" else "hooks.json")
                status = _hook_status(settings_path, command) if command else "missing"
            elif item["kind"] == "directory":
                recorded = [(Path(name), digest) for name, digest in metadata.get("files", {}).items()
                            if Path(name).is_relative_to(path)]
                states = [_file_status(name, digest) for name, digest in recorded]
                status = ("missing" if not states or "missing" in states else
                          "different" if "different" in states else "installed")
            else:
                status = _file_status(path, metadata.get("files", {}).get(str(path)))
            rows.append({"id": item_id, "title": item["title"]["en"],
                         "where": str(path), "status": status})

    stages = map_data["stages"] + map_data["side"]
    referenced = {hook_id for stage in stages for hook_id in stage.get("install", [])
                  if not hook_id.startswith("wf:")}
    referenced.update(hook_id for stage in stages for node in stage.get("nodes", [])
                      for hook_id in node.get("install", []) if not hook_id.startswith("wf:"))
    hook_ids = _installed_hook_ids(home, catalogue)
    for item in catalogue:
        if item["id"] not in referenced:
            continue
        path = keel_home / "hooks" / item["script"]
        rows.append({"id": item["id"], "title": item["title_en"], "where": str(path),
                     "status": "installed" if any(item["id"] in hook_ids[target]
                                                  for target in selected if target in item["targets"])
                     else "not selected"})

    exit_code = int(any(row["status"] in ("missing", "different") for row in rows
                        if row["id"].startswith("wf:")))
    return rows, exit_code
