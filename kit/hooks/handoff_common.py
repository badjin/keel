"""Shared, standalone state helpers for the automatic handoff hooks."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("KEEL_HOME", str(Path.home()))) / ".keel"


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def language() -> str:
    config = read_json(home() / "config.json")
    lang = config.get("ui_language") if config.get("ui_language") is not None else config.get("language")
    return lang if lang in ("en", "ko") else "en"


def usage(session_id: str, transcript_path: str, target: str) -> float | None:
    if target != "codex":
        state = read_json(home() / "state" / "context" / f"{session_id}.json")
        try:
            return float(state["used_percentage"])
        except (KeyError, TypeError, ValueError):
            return None
    window = used = None
    try:
        with open(transcript_path, encoding="utf-8") as stream:
            for line in stream:
                if "model_context_window" not in line and "last_token_usage" not in line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                stack = [item]
                while stack:
                    node = stack.pop()
                    if isinstance(node, list):
                        stack.extend(node)
                    elif isinstance(node, dict):
                        if node.get("model_context_window"):
                            window = int(node["model_context_window"])
                        last = node.get("last_token_usage")
                        if isinstance(last, dict) and last.get("total_tokens") is not None:
                            used = int(last["total_tokens"])
                        stack.extend(node.values())
    except (OSError, TypeError, ValueError):
        return None
    return round(used / window * 100, 1) if window and used is not None else None


def marker_for(cwd: str, target: str | None = None) -> tuple[Path, dict] | None:
    directory = home() / "state" / "auto-handoff"
    best = None
    cwd = str(Path(cwd).resolve())
    pane = os.environ.get("HERDR_PANE_ID") or os.environ.get("TMUX_PANE")
    for path in directory.glob("*.json"):
        marker = read_json(path)
        handoff = Path(marker.get("handoff", ""))
        try:
            age = time.time() - float(marker.get("ts", 0))
            fresh = 0 <= age < 1800
        except (TypeError, ValueError):
            fresh = False
        marker_pane = marker.get("pane")
        pane_matches = marker_pane == pane if marker_pane and pane else (
            marker.get("target") == target if not marker_pane and not pane else True
        )
        if (marker.get("cwd") == cwd and handoff.is_file()
                and handoff.parent == Path(cwd) / ".keel" / "handoff" and fresh
                and not Path(str(path) + ".claimed").exists() and pane_matches
                and (best is None or marker["ts"] > best[1]["ts"])):
            best = (path, marker)
    return best
