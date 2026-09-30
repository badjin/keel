from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from kit import install_hooks
from kit.errors import KitValueError

from . import fsutil, ladder, map_render, rules_block


def _sha(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


def _manifest(repo_root: Path) -> dict:
    return json.loads((repo_root / "kit" / "workflow" / "manifest.json").read_text(encoding="utf-8"))


def _render(text: str, keel_wf: str, kb: Path) -> str:
    return text.replace("{{KEEL_WF}}", keel_wf).replace("{{KB_PATH}}", str(kb))


def _metadata_path(home: Path) -> Path:
    return home / ".keel" / "state" / "workflow" / "installed.json"


def _skill_paths(cli_home: Path, metadata: dict, manifest: dict, has_metadata: bool) -> list[Path]:
    skills_home = cli_home / "skills"
    if has_metadata:
        return [path for name in metadata.get("files", {})
                if (path := Path(name)).name == "SKILL.md" and path.parent.parent == skills_home]
    return [skills_home / item["id"].removeprefix("wf:skill:") / "SKILL.md"
            for item in manifest.get("items", []) if item.get("kind") == "skill"]


def _remove_target(home: Path, target: str, metadata: dict, manifest: dict,
                   has_metadata: bool, removed: list[str], backups: list[str],
                   skipped: list[str]) -> None:
    cli_home = home / (".claude" if target == "claude" else ".codex")
    rules_path = cli_home / ("CLAUDE.md" if target == "claude" else "AGENTS.md")
    if rules_path.exists():
        original = rules_path.read_text(encoding="utf-8")
        detail = metadata.get("rules", {}).get(str(rules_path), {})
        try:
            result = rules_block.remove(original, detail.get("added_newline", False))
        except rules_block.BlockError:
            skipped.append(str(rules_path))
        else:
            if result != original:
                saved = install_hooks._backup(rules_path)
                if saved:
                    backups.append(saved)
                if detail.get("created", False) and not result:
                    rules_path.unlink()
                else:
                    fsutil.atomic_write_text(rules_path, result)
                removed.append(str(rules_path))

    settings_path = cli_home / ("settings.json" if target == "claude" else "hooks.json")
    if settings_path.exists():
        settings = install_hooks._read_json(settings_path, {})
        rest, popped = install_hooks._pop_wf_groups(settings.get("hooks", {}))
        if popped:
            saved = install_hooks._backup(settings_path)
            if saved:
                backups.append(saved)
            settings["hooks"] = rest
            install_hooks._write_json(settings_path, settings)
            removed.append(str(settings_path))

    for skill in _skill_paths(cli_home, metadata, manifest, has_metadata):
        if skill.is_file() and "<!-- keel -->" in skill.read_text(encoding="utf-8"):
            skill.unlink()
            removed.append(str(skill))
            if not any(skill.parent.iterdir()):
                skill.parent.rmdir()

    agents_dir = cli_home / "agents"
    if target == "claude" and agents_dir.is_dir():
        unlinked = False
        for agent in sorted(agents_dir.glob("keel-hotfix-*.md")):
            if "<!-- keel -->" in agent.read_text(encoding="utf-8"):
                agent.unlink()
                removed.append(str(agent))
                unlinked = True
        if unlinked and not any(agents_dir.iterdir()):
            agents_dir.rmdir()


def _copy_workflow(source: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("agents", "hooks", "skills", "__pycache__"))


def install(home: Path, repo_root: Path, targets: list[str], critic_cli: str,
            python: str) -> dict:
    home, repo_root = Path(home).resolve(), Path(repo_root).resolve()
    config_path = home / ".keel" / "config.json"
    config = fsutil.read_json(config_path, {})
    if not config.get("wiki_path"):
        raise KitValueError("wiki_not_ready")
    if critic_cli not in ("claude", "codex"):
        raise KitValueError("workflow_bad_critic")
    selected = [target for target in ("claude", "codex") if target in targets]
    if not selected:
        raise KitValueError("workflow_needs_claude_or_codex")
    kb = Path(config["wiki_path"]).resolve()
    manifest = _manifest(repo_root)
    version = manifest["version"]
    source = repo_root / "kit" / "workflow"
    keel_home = home / ".keel"
    metadata_path = _metadata_path(home)
    old_metadata = fsutil.read_json(metadata_path, {})
    old_rules = old_metadata.get("rules", {})
    written, backups, removed = [], [], []
    files, rules, hooks = {}, {}, {}
    ladder_path = keel_home / "ladder.json"
    if not ladder_path.exists():
        fsutil.atomic_write_json(ladder_path, ladder.DEFAULTS)
    try:
        steps = ladder.load(keel_home).steps
    except ladder.LadderError as exc:
        raise KitValueError("workflow_bad_ladder") from exc

    def record(path: Path) -> None:
        written.append(str(path))
        files[str(path)] = _sha(path.read_bytes())

    def backup(path: Path) -> None:
        saved = install_hooks._backup(path)
        if saved:
            backups.append(saved)

    _copy_workflow(source, keel_home / "workflow")
    shutil.copy2(repo_root / "kit" / "catalogue.json", keel_home / "workflow" / "catalogue.json")
    for path in (keel_home / "workflow").rglob("*"):
        if path.is_file():
            record(path)
    hook_dir = keel_home / "workflow-hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    for source_file in [*sorted((source / "hooks").glob("*.py")), repo_root / "kit" / "hooks" / "_common.py"]:
        destination = hook_dir / source_file.name
        shutil.copy2(source_file, destination)
        record(destination)

    keel_wf = f'"{python}" "{home}/.keel/workflow/keel_wf.py"'
    for target in selected:
        cli_home = home / (".claude" if target == "claude" else ".codex")
        rules_path = cli_home / ("CLAUDE.md" if target == "claude" else "AGENTS.md")
        created = old_rules.get(str(rules_path), {}).get("created", not rules_path.exists())
        original = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""
        backup(rules_path)
        rules_source = source / "rules.en.md"
        body = _render(rules_source.read_text(encoding="utf-8"), keel_wf, kb)
        try:
            updated, added_newline = rules_block.insert(original, body, version)
        except rules_block.BlockError as exc:
            raise rules_block.BlockError(f"{rules_path}: {exc}") from exc
        fsutil.atomic_write_text(rules_path, updated)
        block = f"<!-- keel:workflow:start {version} -->\n{body.rstrip()}\n{rules_block.END}"
        rules[str(rules_path)] = {"sha256": _sha(block.encode("utf-8")), "created": created,
                                  "added_newline": old_rules.get(str(rules_path), {}).get("added_newline", added_newline)}
        written.append(str(rules_path))

        for item in manifest["items"]:
            if item["kind"] != "skill" or target not in item["targets"]:
                continue
            skill_source = source / "skills" / item["id"].removeprefix("wf:skill:") / "SKILL.md"
            dest = cli_home / "skills" / skill_source.parent.name / "SKILL.md"
            if dest.exists() and "<!-- keel -->" not in dest.read_text(encoding="utf-8"):
                continue
            if dest.exists() and _sha(dest.read_bytes()) != old_metadata.get("files", {}).get(str(dest)):
                backup(dest)
            fsutil.atomic_write_text(dest, _render(skill_source.read_text(encoding="utf-8"), keel_wf, kb))
            record(dest)

        if target == "claude":
            template = (source / "agents" / "keel-hotfix.md").read_text(encoding="utf-8")
            agents_home = cli_home / "agents"
            for number, step in enumerate(steps, 1):
                dest = agents_home / f"keel-hotfix-{number}.md"
                if dest.exists() and "<!-- keel -->" not in dest.read_text(encoding="utf-8"):
                    continue
                if dest.exists() and _sha(dest.read_bytes()) != old_metadata.get("files", {}).get(str(dest)):
                    backup(dest)
                text = (template.replace("{{STEP}}", str(number))
                        .replace("{{MODEL}}", step.claude["model"])
                        .replace("{{EFFORT}}", step.claude["effort"]))
                fsutil.atomic_write_text(dest, text)
                record(dest)
            for stale in sorted(agents_home.glob("keel-hotfix-*.md")):
                number = stale.stem.removeprefix("keel-hotfix-")
                if (number.isdigit() and int(number) > len(steps)
                        and "<!-- keel -->" in stale.read_text(encoding="utf-8")):
                    stale.unlink()
                    removed.append(str(stale))

        settings_path = cli_home / ("settings.json" if target == "claude" else "hooks.json")
        existed = settings_path.exists()
        settings = install_hooks._read_json(settings_path, {} if target == "claude" else {"hooks": {}})
        backup(settings_path)
        if not existed:
            install_hooks._write_sentinel_if_missing(settings_path, {} if target == "claude" else {"hooks": {}})
        original_hooks = settings.get("hooks", {})
        rest, _ = install_hooks._pop_wf_groups(original_hooks)
        entries = [
            {"event": item["event"], "matcher": item["matcher"],
             "command": f'"{python}" "{home}/.keel/workflow-hooks/{item["script"]}" {target}',
             "timeout": item["timeout"]}
            for item in manifest["items"] if item["kind"] == "hook" and target in item["targets"]
        ]
        settings["hooks"] = install_hooks._add_handlers(rest, entries, omit_matcher_always=target == "codex")
        install_hooks._write_json(settings_path, settings)
        hooks[target] = [entry["command"] for entry in entries]
        written.append(str(settings_path))
        if target == "codex":
            toml_path = cli_home / "config.toml"
            old_toml = toml_path.read_text(encoding="utf-8") if toml_path.exists() else ""
            backup(toml_path)
            fsutil.atomic_write_text(toml_path, install_hooks._ensure_features_hooks_true(old_toml))
            written.append(str(toml_path))

    for target in ("claude", "codex"):
        if target not in selected:
            _remove_target(home, target, old_metadata, manifest, metadata_path.exists(),
                           removed, backups, [])

    config["workflow"] = {"installed": True, "version": version, "language": "en",
                          "targets": selected, "critic_cli": critic_cli, "keel_wf": keel_wf}
    fsutil.atomic_write_json(config_path, config)
    fsutil.atomic_write_json(_metadata_path(home), {"version": version, "files": files,
                                                   "rules": rules, "hooks": hooks})
    errors = []
    try:
        written.append(str(map_render.write_map(home, repo_root=repo_root)))
    except Exception as exc:  # The map is supplementary to a working installation.
        errors.append(f"harness map: {exc}")
    return {"written": written, "removed": removed, "backups": backups,
            "codex": "codex" in selected, "errors": errors}


def uninstall(home: Path) -> dict:
    home = Path(home).resolve()
    keel_home = home / ".keel"
    config_path = keel_home / "config.json"
    config = fsutil.read_json(config_path, {})
    metadata_path = _metadata_path(home)
    metadata = fsutil.read_json(metadata_path, {})
    manifest = fsutil.read_json(keel_home / "workflow" / "manifest.json", {})
    removed, backups, skipped = [], [], []
    for target in ("claude", "codex"):
        _remove_target(home, target, metadata, manifest, metadata_path.exists(),
                       removed, backups, skipped)

    for name in ("workflow", "workflow-hooks"):
        path = keel_home / name
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path))
    config.pop("workflow", None)
    fsutil.atomic_write_json(config_path, config)
    metadata_path.unlink(missing_ok=True)
    return {"removed": removed, "skipped": skipped, "backups": backups}


def status(home: Path) -> dict:
    config = fsutil.read_json(Path(home) / ".keel" / "config.json", {})
    return config.get("workflow") or {"installed": False}
