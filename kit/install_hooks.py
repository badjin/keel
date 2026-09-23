"""Merges the kit's chosen hooks into Claude Code settings.json and Codex hooks.json."""
from __future__ import annotations
import datetime
import json
import re
import shutil
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

from . import skills_install
from .errors import KitValueError

KIT_MARKER = "/.keel/hooks/"


def load_catalogue(repo_root: Path) -> list[dict]:
    path = Path(repo_root) / "kit" / "catalogue.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _backup(path: Path) -> str | None:
    if not path.exists():
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = path.with_name(path.name + f".bak-{ts}")
    shutil.copy(path, backup_path)
    return str(backup_path)


def _write_sentinel_if_missing(path: Path, content: dict) -> None:
    """Writes `<path>.bak-before-kit` only if it does not already exist.

    Caller only invokes this when `path` itself did not exist before this
    install, so the sentinel always captures the true pre-kit state.
    """
    sentinel = path.with_name(path.name + ".bak-before-kit")
    if not sentinel.exists():
        _write_json(sentinel, content)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _is_kit_command(cmd: str) -> bool:
    return KIT_MARKER in cmd or "/.llm-wiki-kit/hooks/" in cmd


def _strip_kit_handlers(hooks_by_event: dict) -> dict:
    """Removes kit-owned command entries; drops groups/events left empty."""
    result = {}
    for event, groups in hooks_by_event.items():
        new_groups = []
        for grp in groups:
            handlers = grp.get("hooks", [])
            kept = [h for h in handlers if not _is_kit_command(h.get("command", ""))]
            if kept:
                new_grp = dict(grp)
                new_grp["hooks"] = kept
                new_groups.append(new_grp)
        if new_groups:
            result[event] = new_groups
    return result


def _add_handlers(hooks_by_event: dict, entries: list[dict], omit_matcher_always: bool = False) -> dict:
    result = {k: list(v) for k, v in hooks_by_event.items()}
    for entry in entries:
        event = entry["event"]
        matcher = entry["matcher"]
        cmd = entry["command"]
        timeout = entry["timeout"]
        handler = {"type": "command", "command": cmd, "timeout": timeout}
        group = {"hooks": [handler]}
        if not omit_matcher_always and matcher:
            group = {"matcher": matcher, "hooks": [handler]}
        result.setdefault(event, [])
        result[event].append(group)
    return result


def _hook_command(python: str, home: Path, script: str, target: str = "") -> str:
    suffix = f" {target}" if script in ("handoff_gate.py", "handoff_restore.py") else ""
    return f'"{python}" "{home}/.keel/hooks/{script}"{suffix}'


def _statusline_command(python: str, home: Path, target: str) -> str:
    return f'"{python}" "{home}/.keel/hooks/handoff_statusline.py" {target}'


def _set_statusline(settings: dict, home: Path, python: str, target: str) -> None:
    originals_path = home / ".keel" / "state" / "statusline.json"
    originals = _read_json(originals_path, {})
    current = settings.get("statusLine")
    if target not in originals and not (isinstance(current, dict) and "handoff_statusline.py" in current.get("command", "")):
        originals[target] = current
        _write_json(originals_path, originals)
    settings["statusLine"] = {"type": "command", "command": _statusline_command(python, home, target)}


def _restore_statusline(settings: dict, home: Path, target: str) -> None:
    current = settings.get("statusLine")
    if not (isinstance(current, dict) and "handoff_statusline.py" in current.get("command", "")):
        return
    originals_path = home / ".keel" / "state" / "statusline.json"
    originals = _read_json(originals_path, {})
    if target not in originals:
        return
    original = originals.pop(target)
    if original is None:
        settings.pop("statusLine", None)
    else:
        settings["statusLine"] = original
    _write_json(originals_path, originals)


GROK_STATUS_SECTION = re.compile(
    r"(?ms)^[ \t]*\[[ \t]*ui[ \t]*\.[ \t]*status_line[ \t]*\][^\n]*\n.*?(?=^[ \t]*\[|\Z)"
)


def _grok_statusline_without_tomllib(text: str) -> dict | None:
    headers = re.findall(r"(?m)^[ \t]*\[[ \t]*ui[ \t]*\.[ \t]*status_line[ \t]*\][ \t]*(?:#.*)?$", text)
    if len(headers) > 1:
        return None
    for line in text.splitlines():
        if "status_line" in line and line not in headers and re.match(
            r"^[ \t]*(?:\[[^\]]*status_line[^\]]*\]|[^#=\n]*\bstatus_line\b[^=\n]*=)", line
        ):
            return None
    if not headers:
        return {}
    section = GROK_STATUS_SECTION.search(text)
    if section is None:
        return None
    commands = re.findall(r'(?m)^[ \t]*command[ \t]*=[ \t]*("(?:\\.|[^"\\])*")[ \t]*(?:#.*)?$', section.group(0))
    if len(commands) != 1 or len(re.findall(r"(?m)^[ \t]*command[ \t]*=", section.group(0))) != 1:
        return None
    try:
        command = json.loads(commands[0])
    except json.JSONDecodeError:
        return None
    return {"status_line": {"command": command}}


def _set_grok_statusline(path: Path, home: Path, python: str) -> bool:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if tomllib is None:
        ui = _grok_statusline_without_tomllib(text)
        if ui is None:
            return False
    else:
        try:
            ui = tomllib.loads(text).get("ui", {})
        except tomllib.TOMLDecodeError:
            return False
    original = GROK_STATUS_SECTION.search(text)
    if isinstance(ui, dict) and "status_line" in ui:
        status = ui["status_line"]
        if not (original and isinstance(status, dict) and isinstance(status.get("command"), str)):
            return False
    state_path = home / ".keel" / "state" / "grok-statusline.json"
    if not state_path.exists():
        state_path.parent.mkdir(parents=True, exist_ok=True)
        command = ui["status_line"]["command"] if original else None
        _write_json(state_path, {"section": original.group(0) if original else "", "command": command})
        originals = _read_json(home / ".keel" / "state" / "statusline.json", {})
        originals["grok"] = {"command": command} if command else None
        _write_json(home / ".keel" / "state" / "statusline.json", originals)
    replacement = "[ui.status_line]\ntype = \"command\"\ncommand = " + json.dumps(_statusline_command(python, home, "grok")) + "\n"
    if original:
        text = text[:original.start()] + replacement + text[original.end():]
    else:
        text += ("\n" if text and not text.endswith("\n") else "") + "\n" + replacement
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _restore_grok_statusline(path: Path, home: Path) -> None:
    state_path = home / ".keel" / "state" / "grok-statusline.json"
    if not path.exists() or not state_path.exists():
        return
    text = path.read_text(encoding="utf-8")
    match = GROK_STATUS_SECTION.search(text)
    if not match or "handoff_statusline.py" not in match.group(0):
        return
    original = _read_json(state_path, {}).get("section", "")
    path.write_text(text[:match.start()] + original + text[match.end():], encoding="utf-8")
    state_path.unlink()
    originals_path = home / ".keel" / "state" / "statusline.json"
    originals = _read_json(originals_path, {})
    originals.pop("grok", None)
    _write_json(originals_path, originals)


def _select_hooks(catalogue: list[dict], hook_ids: list[str], target: str) -> list[dict]:
    by_id = {h["id"]: h for h in catalogue}
    unknown = [hid for hid in hook_ids if hid not in by_id]
    if unknown:
        raise KitValueError("unknown_hook_id", ids=", ".join(unknown))
    return [by_id[hid] for hid in hook_ids if target in by_id[hid]["targets"]]


def _ensure_features_hooks_true(text: str) -> str:
    if not text.strip():
        return "[features]\nhooks = true\n"

    lines = text.split("\n")
    feature_start = None
    for i, line in enumerate(lines):
        if line.strip() == "[features]":
            feature_start = i
            break

    if feature_start is None:
        suffix = "" if text.endswith("\n") else "\n"
        return text + suffix + "\n[features]\nhooks = true\n"

    section_end = len(lines)
    for i in range(feature_start + 1, len(lines)):
        if re.match(r"^\s*\[", lines[i]):
            section_end = i
            break

    hooks_line_re = re.compile(r"^\s*hooks\s*=")
    replaced = False
    for i in range(feature_start + 1, section_end):
        if hooks_line_re.match(lines[i]):
            lines[i] = "hooks = true"
            replaced = True
            break

    if not replaced:
        lines.insert(feature_start + 1, "hooks = true")

    return "\n".join(lines)


def _parse_codex_trust_state(text: str) -> dict[str, bool]:
    """Reads `[hooks.state."<key>"]` sections from Codex's config.toml the
    same line-scanning way `_ensure_features_hooks_true` reads `[features]`
    (no tomllib dependency). A section counts as trusted if it has a
    `trusted_hash` line before the next `[` header."""
    result: dict[str, bool] = {}
    lines = text.split("\n")
    header_re = re.compile(r'^\s*\[hooks\.state\."(.*)"\]\s*$')
    hash_re = re.compile(r'^\s*trusted_hash\s*=')
    i = 0
    n = len(lines)
    while i < n:
        m = header_re.match(lines[i])
        if not m:
            i += 1
            continue
        key = m.group(1)
        i += 1
        trusted = False
        while i < n and not re.match(r"^\s*\[", lines[i]):
            if hash_re.match(lines[i]):
                trusted = True
            i += 1
        result[key] = trusted
    return result


def codex_trust_status(home: Path, repo_root: Path) -> dict:
    """Reports, per kit-installed Codex hook, whether Codex's config.toml
    already has a `trusted_hash` for it under `[hooks.state."<key>"]`.

    The key is built the same way Codex derives it from `~/.codex/hooks.json`:
    `<hooks.json path>:<event>:<group index>:<handler index>` — the group and
    handler indexes are each kit entry's position in `install()`'s
    hooks_by_event[event] list (one handler per group, since `_add_handlers`
    appends a fresh group per entry when writing Codex's file)."""
    home = Path(home)
    hooks_json_path = home / ".codex" / "hooks.json"
    hooks_json = _read_json(hooks_json_path, {"hooks": {}})
    hooks_by_event = hooks_json.get("hooks", {})

    config_toml_path = home / ".codex" / "config.toml"
    text = config_toml_path.read_text(encoding="utf-8") if config_toml_path.exists() else ""
    trust_state = _parse_codex_trust_state(text)

    script_to_id = {}
    catalogue_path = Path(repo_root) / "kit" / "catalogue.json"
    if catalogue_path.exists():
        catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
        script_to_id = {h["script"]: h["id"] for h in catalogue}

    key_base = str(hooks_json_path)
    hooks: list[dict] = []
    for event, groups in hooks_by_event.items():
        for group_idx, grp in enumerate(groups):
            for handler_idx, h in enumerate(grp.get("hooks", [])):
                cmd = h.get("command", "")
                if not _is_kit_command(cmd):
                    continue
                script = cmd.rsplit("/", 1)[-1].split('"', 1)[0]
                hook_id = script_to_id.get(script, script)
                key = f"{key_base}:{event}:{group_idx}:{handler_idx}"
                hooks.append({"id": hook_id, "trusted": trust_state.get(key, False)})

    trusted_count = sum(1 for h in hooks if h["trusted"])
    return {
        "hooks": hooks,
        "trusted_count": trusted_count,
        "total_count": len(hooks),
        "remaining": len(hooks) - trusted_count,
    }


def install(home: Path, repo_root: Path, hook_ids: list[str], targets: list[str], python: str = "python3", handoff_threshold: int = 60, handoff_targets: list[str] | None = None, ui_language: str | None = None) -> dict:
    home = Path(home)
    repo_root = Path(repo_root)
    catalogue = load_catalogue(repo_root)
    by_id = {h["id"]: h for h in catalogue}
    unknown = [hid for hid in hook_ids if hid not in by_id]
    if unknown:
        raise KitValueError("unknown_hook_id", ids=", ".join(unknown))
    if not isinstance(handoff_threshold, int) or not 40 <= handoff_threshold <= 80:
        raise KitValueError("invalid_handoff_threshold")
    handoff_targets = list(targets) if handoff_targets is None else handoff_targets
    if "grok" in handoff_targets and handoff_threshold > 80:
        raise KitValueError("invalid_handoff_threshold")
    handoff_ids = {"automatic-handoff", "automatic-handoff-restore"}
    if "automatic-handoff" in hook_ids:
        hook_ids = list(dict.fromkeys([*hook_ids, "automatic-handoff-restore"]))
    else:
        hook_ids = [hid for hid in hook_ids if hid not in handoff_ids]

    kit_home = home / ".keel"
    hooks_dir = kit_home / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    src_hooks_dir = repo_root / "kit" / "hooks"
    for src_file in src_hooks_dir.glob("*.py"):
        shutil.copy(src_file, hooks_dir / src_file.name)
    for src_file in src_hooks_dir.glob("*.sh"):
        shutil.copy(src_file, hooks_dir / src_file.name)

    config_path = kit_home / "config.json"
    config = _read_json(config_path, {})
    if ui_language in ("en", "ko"):
        config["ui_language"] = ui_language
    config["handoff_threshold"] = handoff_threshold
    config["handoff_targets"] = [t for t in targets if t in handoff_targets and "automatic-handoff" in hook_ids]
    config["grok_via_claude"] = "grok" in targets and "claude" in targets
    _write_json(config_path, config)

    backups: list[str] = []
    installed = {"claude": [], "codex": [], "grok": []}
    grok_statusline_manual = False

    if "claude" in targets:
        settings_path = home / ".claude" / "settings.json"
        existed = settings_path.exists()
        settings = _read_json(settings_path, {})
        backup = _backup(settings_path)
        if backup:
            backups.append(backup)
        if not existed:
            _write_sentinel_if_missing(settings_path, {})
        selected = [h for h in _select_hooks(catalogue, hook_ids, "claude")
                    if h["id"] not in handoff_ids or "claude" in handoff_targets or ("grok" in targets and "grok" in handoff_targets)]
        hooks_by_event = settings.get("hooks", {})
        hooks_by_event = _strip_kit_handlers(hooks_by_event)
        entries = [
            {
                "event": h["event"],
                "matcher": h["matcher"],
                "command": _hook_command(python, home, h["script"], "claude"),
                "timeout": h["timeout"],
            }
            for h in selected
        ]
        hooks_by_event = _add_handlers(hooks_by_event, entries, omit_matcher_always=False)
        settings["hooks"] = hooks_by_event
        if "automatic-handoff" in hook_ids and "claude" in handoff_targets:
            _set_statusline(settings, home, python, "claude")
        else:
            _restore_statusline(settings, home, "claude")
        _write_json(settings_path, settings)
        installed["claude"] = [h["id"] for h in selected]

    if "codex" in targets:
        hooks_json_path = home / ".codex" / "hooks.json"
        existed = hooks_json_path.exists()
        hooks_json = _read_json(hooks_json_path, {"hooks": {}})
        backup = _backup(hooks_json_path)
        if backup:
            backups.append(backup)
        if not existed:
            _write_sentinel_if_missing(hooks_json_path, {"hooks": {}})
        selected = [h for h in _select_hooks(catalogue, hook_ids, "codex")
                    if h["id"] not in handoff_ids or "codex" in handoff_targets]
        hooks_by_event = hooks_json.get("hooks", {})
        hooks_by_event = _strip_kit_handlers(hooks_by_event)
        entries = [
            {
                "event": h["event"],
                "matcher": h["matcher"],
                "command": _hook_command(python, home, h["script"], "codex"),
                "timeout": h["timeout"],
            }
            for h in selected
        ]
        hooks_by_event = _add_handlers(hooks_by_event, entries, omit_matcher_always=True)
        hooks_json["hooks"] = hooks_by_event
        _write_json(hooks_json_path, hooks_json)
        installed["codex"] = [h["id"] for h in selected]

        config_toml_path = home / ".codex" / "config.toml"
        if config_toml_path.exists():
            backup = _backup(config_toml_path)
            if backup:
                backups.append(backup)
            text = config_toml_path.read_text(encoding="utf-8")
        else:
            text = ""
        new_text = _ensure_features_hooks_true(text)
        config_toml_path.parent.mkdir(parents=True, exist_ok=True)
        config_toml_path.write_text(new_text, encoding="utf-8")

    grok_via_claude = "grok" in targets and "claude" in targets
    if "grok" in targets:
        grok_path = home / ".grok" / "hooks" / "keel.json"
        if grok_via_claude:
            # Grok reads ~/.claude/settings.json hooks by default when Claude
            # Code is also selected, so writing a kit-owned keel.json here
            # would make every hook run twice. Drop a stale one from an
            # earlier grok-only install instead of writing a fresh one.
            if grok_path.exists():
                backup = _backup(grok_path)
                if backup:
                    backups.append(backup)
                grok_path.unlink()
            installed["grok"] = [
                h["id"] for h in selected
            ]
        else:
            existed = grok_path.exists()
            grok_json = _read_json(grok_path, {"hooks": {}})
            backup = _backup(grok_path)
            if backup:
                backups.append(backup)
            if not existed:
                _write_sentinel_if_missing(grok_path, {"hooks": {}})
            # Grok has no "grok" entry in catalogue targets — it reads the
            # same hook JSON format Claude Code does, so it reuses Claude's
            # hook selection.
            selected = [h for h in _select_hooks(catalogue, hook_ids, "claude")
                        if h["id"] not in handoff_ids or "grok" in handoff_targets]
            hooks_by_event = grok_json.get("hooks", {})
            hooks_by_event = _strip_kit_handlers(hooks_by_event)
            entries = [
                {
                    "event": h["event"],
                    "matcher": h["matcher"],
                    "command": _hook_command(python, home, h["script"], "grok"),
                    "timeout": h["timeout"],
                }
                for h in selected
            ]
            hooks_by_event = _add_handlers(hooks_by_event, entries, omit_matcher_always=False)
            grok_json["hooks"] = hooks_by_event
            _write_json(grok_path, grok_json)
            installed["grok"] = [h["id"] for h in selected]

        grok_settings_path = home / ".grok" / "config.toml"
        if "automatic-handoff" in hook_ids and "grok" in handoff_targets:
            backup = _backup(grok_settings_path)
            if backup:
                backups.append(backup)
            grok_statusline_manual = not _set_grok_statusline(grok_settings_path, home, python)
        else:
            _restore_grok_statusline(grok_settings_path, home)

    active_handoff_targets = [t for t in targets if t in handoff_targets and "automatic-handoff" in hook_ids]
    skills_install.install_handoff_skill(home, active_handoff_targets, config.get("ui_language", "en"))
    skills_install.remove_handoff_skill(home, [t for t in targets if t not in active_handoff_targets])

    return {
        "claude": installed["claude"],
        "codex": installed["codex"],
        "grok": installed["grok"],
        "grok_via_claude": grok_via_claude,
        "grok_statusline_manual": grok_statusline_manual,
        "backups": backups,
    }


def uninstall(home: Path, targets: list[str], remove_skills: bool = False) -> dict:
    home = Path(home)
    result = {"claude": 0, "codex": 0, "grok": 0, "backups": [], "skills_removed": []}

    if "claude" in targets:
        settings_path = home / ".claude" / "settings.json"
        settings = _read_json(settings_path, None)
        if settings is not None:
            backup = _backup(settings_path)
            if backup:
                result["backups"].append(backup)
            before = sum(
                1
                for grp in [g for groups in settings.get("hooks", {}).values() for g in groups]
                for h in grp.get("hooks", [])
                if _is_kit_command(h.get("command", ""))
            )
            settings["hooks"] = _strip_kit_handlers(settings.get("hooks", {}))
            _restore_statusline(settings, home, "claude")
            _write_json(settings_path, settings)
            result["claude"] = before

    if "codex" in targets:
        hooks_json_path = home / ".codex" / "hooks.json"
        hooks_json = _read_json(hooks_json_path, None)
        if hooks_json is not None:
            backup = _backup(hooks_json_path)
            if backup:
                result["backups"].append(backup)
            before = sum(
                1
                for grp in [g for groups in hooks_json.get("hooks", {}).values() for g in groups]
                for h in grp.get("hooks", [])
                if _is_kit_command(h.get("command", ""))
            )
            hooks_json["hooks"] = _strip_kit_handlers(hooks_json.get("hooks", {}))
            _write_json(hooks_json_path, hooks_json)
            result["codex"] = before

    if "grok" in targets:
        _restore_grok_statusline(home / ".grok" / "config.toml", home)
        grok_path = home / ".grok" / "hooks" / "keel.json"
        if grok_path.exists():
            backup = _backup(grok_path)
            if backup:
                result["backups"].append(backup)
            grok_json = _read_json(grok_path, {"hooks": {}})
            before = sum(
                1
                for grp in [g for groups in grok_json.get("hooks", {}).values() for g in groups]
                for h in grp.get("hooks", [])
                if _is_kit_command(h.get("command", ""))
            )
            # The whole file is kit-owned (unlike settings.json/hooks.json,
            # which mix in the user's own entries), so uninstall removes it
            # outright rather than stripping handlers in place.
            grok_path.unlink()
            result["grok"] = before

    if remove_skills:
        removed = skills_install.remove_skills(home, targets)
        result["skills_removed"] = [str(p) for p in removed]
    result["skills_removed"] += [str(p) for p in skills_install.remove_handoff_skill(home, targets)]

    return result


def installed_ids(home: Path) -> dict:
    home = Path(home)
    result = {"claude": [], "codex": [], "grok": []}

    settings = _read_json(home / ".claude" / "settings.json", {})
    for groups in settings.get("hooks", {}).values():
        for grp in groups:
            for h in grp.get("hooks", []):
                cmd = h.get("command", "")
                if _is_kit_command(cmd):
                    script = cmd.rsplit("/", 1)[-1].split('"', 1)[0]
                    result["claude"].append(script)

    hooks_json = _read_json(home / ".codex" / "hooks.json", {"hooks": {}})
    for groups in hooks_json.get("hooks", {}).values():
        for grp in groups:
            for h in grp.get("hooks", []):
                cmd = h.get("command", "")
                if _is_kit_command(cmd):
                    script = cmd.rsplit("/", 1)[-1].split('"', 1)[0]
                    result["codex"].append(script)

    grok_json = _read_json(home / ".grok" / "hooks" / "keel.json", {"hooks": {}})
    for groups in grok_json.get("hooks", {}).values():
        for grp in groups:
            for h in grp.get("hooks", []):
                cmd = h.get("command", "")
                if _is_kit_command(cmd):
                    script = cmd.rsplit("/", 1)[-1].split('"', 1)[0]
                    result["grok"].append(script)

    script_to_id = {}
    catalogue_path = Path(__file__).resolve().parent / "catalogue.json"
    if catalogue_path.exists():
        catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
        script_to_id = {h["script"]: h["id"] for h in catalogue}

    result["claude"] = [script_to_id.get(s, s) for s in result["claude"]]
    result["codex"] = [script_to_id.get(s, s) for s in result["codex"]]
    result["grok"] = [script_to_id.get(s, s) for s in result["grok"]]
    return result
