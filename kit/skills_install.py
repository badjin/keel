"""Installs the kit's wiki skills into the chosen CLI's skills directory."""
from __future__ import annotations
from pathlib import Path

KIT_MARKER = "<!-- keel -->"
LEGACY_MARKER = "<!-- llm-wiki-kit -->"
SKILLS_DIR = Path(__file__).resolve().parent / "skills"
SKILL_NAMES = ("kb", "kb-ingest", "kb-lint")
LEGACY_SKILL_NAMES = ("wiki", "wiki-ingest", "wiki-lint")

TARGET_BASE = {"claude": ".claude", "codex": ".codex", "grok": ".grok"}


def _kit_owned(path: Path) -> bool:
    head = path.read_text(encoding="utf-8").splitlines()[:10]
    return any(KIT_MARKER in line or LEGACY_MARKER in line for line in head)


def install_skills(home: Path, wiki_path: Path, targets: list) -> list:
    home = Path(home)
    wiki_path = Path(wiki_path)
    written: list = []

    # Grok reads ~/.claude/skills/ by default, so a separate ~/.grok/skills/
    # copy is only written when Claude Code was not also selected.
    effective_targets = [
        t for t in targets if not (t == "grok" and "claude" in targets)
    ]
    remove_legacy_skills(home, targets)

    for name in SKILL_NAMES:
        src_path = SKILLS_DIR / name / "SKILL.md"
        content = src_path.read_text(encoding="utf-8").replace("{{WIKI_PATH}}", str(wiki_path))

        for target in effective_targets:
            base = TARGET_BASE.get(target)
            if base is None:
                continue
            dest = home / base / "skills" / name / "SKILL.md"
            if dest.exists():
                if not _kit_owned(dest):
                    continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            written.append(dest)

    return written


def remove_legacy_skills(home: Path, targets: list) -> list:
    removed = []
    for target in targets:
        base = TARGET_BASE.get(target)
        if base is None:
            continue
        for name in LEGACY_SKILL_NAMES:
            dest = Path(home) / base / "skills" / name / "SKILL.md"
            if dest.exists() and _kit_owned(dest):
                dest.unlink()
                removed.append(dest)
                if not any(dest.parent.iterdir()):
                    dest.parent.rmdir()
    return removed


def remove_skills(home: Path, targets: list) -> list:
    """Removes kit-marked SKILL.md files (and their now-empty skill folders)
    for the given targets. A SKILL.md without the kit marker in its first
    10 lines (user-authored) is left alone."""
    home = Path(home)
    removed: list = remove_legacy_skills(home, targets)

    for target in targets:
        base = TARGET_BASE.get(target)
        if base is None:
            continue
        for name in SKILL_NAMES:
            dest = home / base / "skills" / name / "SKILL.md"
            if not dest.exists():
                continue
            if not _kit_owned(dest):
                continue
            dest.unlink()
            removed.append(dest)
            skill_dir = dest.parent
            if skill_dir.is_dir() and not any(skill_dir.iterdir()):
                skill_dir.rmdir()

    return removed


def install_handoff_skill(home: Path, targets: list[str], language: str = "en") -> list[Path]:
    filename = "SKILL.ko.md" if language == "ko" else "SKILL.md"
    source = (SKILLS_DIR / "handoff" / filename).read_text(encoding="utf-8")
    written = []
    for target in targets:
        if target == "grok" and "claude" in targets:
            continue
        base = TARGET_BASE.get(target)
        if not base:
            continue
        dest = Path(home) / base / "skills" / "handoff" / "SKILL.md"
        if dest.exists() and not _kit_owned(dest):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source, encoding="utf-8")
        written.append(dest)
    return written


def remove_handoff_skill(home: Path, targets: list[str]) -> list[Path]:
    removed = []
    for target in targets:
        base = TARGET_BASE.get(target)
        if not base:
            continue
        dest = Path(home) / base / "skills" / "handoff" / "SKILL.md"
        if dest.exists() and _kit_owned(dest):
            dest.unlink()
            removed.append(dest)
            if not any(dest.parent.iterdir()):
                dest.parent.rmdir()
    return removed
