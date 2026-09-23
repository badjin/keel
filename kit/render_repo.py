"""Renders per-repo wiki pages (index / timeline / areas) from collected changes."""
from __future__ import annotations
import re
from collections import Counter, defaultdict
from pathlib import Path

from kit.history import Change, month_of
from kit.wiki_init import add_index_links

LABELS = {
    "ko": {
        "period": "최근 {n}일",
        "branch": "브랜치",
        "source": "출처",
        "landed_changes": "반영된 변경",
        "merges": "머지/PR",
        "direct_commits": "직접 커밋",
        "contributors": "기여자",
        "areas_heading": "영역",
        "months_heading": "타임라인",
        "no_changes": "이 기간에 메인 브랜치에 들어온 변경이 없습니다.",
        "back": "저장소 색인으로",
        "summary_heading": "요약",
        "repositories_section": "저장소",
    },
    "en": {
        "period": "last {n} days",
        "branch": "Branch",
        "source": "Source",
        "landed_changes": "Landed changes",
        "merges": "Merges/PRs",
        "direct_commits": "Direct commits",
        "contributors": "Contributors",
        "areas_heading": "Areas",
        "months_heading": "Timeline",
        "no_changes": "No changes landed on this branch in this period.",
        "back": "Back to repo index",
        "summary_heading": "Summary",
        "repositories_section": "Repositories",
    },
}


def _lang(language: str) -> str:
    return language if language in ("ko", "en") else "ko"


def _repo_parts(repo: str):
    if "/" in repo:
        owner, name = repo.split("/", 1)
    else:
        owner, name = "", repo
    return owner, name


def _resolve_slug(wiki_path: Path, repo: str) -> str:
    owner, name = _repo_parts(repo)
    base_slug = name.lower()
    marker = f"<!-- repo: {repo} -->"
    existing_index = wiki_path / "repos" / base_slug / "index.md"
    if existing_index.exists():
        text = existing_index.read_text(encoding="utf-8", errors="replace")
        if marker not in text:
            if owner:
                return f"{owner.lower()}__{name.lower()}"
            return f"local__{name.lower()}"
    return base_slug


_FORBIDDEN_AREA_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def _base_area_slug(area: str) -> str:
    if area == "(root)":
        return "_root"
    cleaned = _FORBIDDEN_AREA_CHARS_RE.sub("", area).strip()
    if cleaned.startswith("."):
        cleaned = "dot-" + cleaned.lstrip(".")
    cleaned = cleaned.lower()
    return cleaned or "_root"


def _resolve_area_slugs(areas: list) -> dict:
    groups = defaultdict(list)
    for a in areas:
        groups[_base_area_slug(a)].append(a)
    slug_map = {}
    for base, names in groups.items():
        for i, name in enumerate(sorted(names)):
            slug_map[name] = base if i == 0 else f"{base}-{i + 1}"
    return slug_map


def _escape_wiki_text(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]").replace("|", "\\|")


def _change_line(c: Change, slug: str, area_slug_map: dict) -> str:
    pr_part = f"[#{c.pr}] " if c.pr else ""
    title = _escape_wiki_text(c.title)
    areas_part = " · ".join(f"[[repos/{slug}/areas/{area_slug_map[a]}|{a}]]" for a in c.areas)
    return f"- {c.date} · {pr_part}{title} · {c.author} · {areas_part}"


def render_repo(
    wiki_path: Path,
    repo: str,
    branch: str,
    since_days: int,
    changes: list,
    language: str,
    summaries: dict = None,
    source: str = "",
) -> list:
    wiki_path = Path(wiki_path)
    lang = _lang(language)
    labels = LABELS[lang]
    summaries = summaries or {}

    slug = _resolve_slug(wiki_path, repo)
    repo_dir = wiki_path / "repos" / slug
    repo_dir.mkdir(parents=True, exist_ok=True)
    marker = f"<!-- repo: {repo} -->"

    timeline_dir = repo_dir / "timeline"
    areas_dir = repo_dir / "areas"
    for stale_dir in (timeline_dir, areas_dir):
        if stale_dir.exists():
            for stale_file in stale_dir.glob("*.md"):
                stale_file.unlink()

    written = []

    if not changes:
        lines = [marker, f"# {repo}", ""]
        if source:
            lines.append(f"{labels['source']}: {source}")
            lines.append("")
        lines.append(f"{labels['branch']}: {branch}")
        lines.append(labels["period"].format(n=since_days))
        lines.append("")
        lines.append(labels["no_changes"])
        lines.append("")
        index_path = repo_dir / "index.md"
        index_path.write_text("\n".join(lines), encoding="utf-8")
        written.append(index_path)
        add_index_links(wiki_path, labels["repositories_section"], [f"repos/{slug}/index|{repo}"])
        return written

    area_counter = Counter()
    for c in changes:
        for a in c.areas:
            area_counter[a] += 1
    top_areas = [a for a, _ in area_counter.most_common(10)]
    all_areas = sorted({a for c in changes for a in c.areas})
    area_slug_map = _resolve_area_slugs(all_areas)

    months = []
    seen_months = set()
    for c in changes:
        ym = month_of(c)
        if ym not in seen_months:
            seen_months.add(ym)
            months.append(ym)

    contributors = sorted({c.author for c in changes})
    merges_count = sum(1 for c in changes if c.is_merge)
    direct_count = len(changes) - merges_count

    lines = [marker, f"# {repo}", ""]
    if source:
        lines.append(f"{labels['source']}: {source}")
        lines.append("")
    if summaries.get("index"):
        lines.append(f"## {labels['summary_heading']}")
        lines.append("")
        lines.append(summaries["index"])
        lines.append("")
    lines.append(f"{labels['branch']}: {branch}")
    lines.append(labels["period"].format(n=since_days))
    lines.append("")
    lines.append(f"- {labels['landed_changes']}: {len(changes)}")
    lines.append(f"- {labels['merges']}: {merges_count}")
    lines.append(f"- {labels['direct_commits']}: {direct_count}")
    lines.append(f"- {labels['contributors']}: {len(contributors)}")
    lines.append("")
    lines.append(f"## {labels['areas_heading']}")
    lines.append("")
    for a in top_areas:
        lines.append(f"- [[repos/{slug}/areas/{area_slug_map[a]}|{a}]]")
    lines.append("")
    lines.append(f"## {labels['months_heading']}")
    lines.append("")
    for ym in months:
        lines.append(f"- [[repos/{slug}/timeline/{ym}|{ym}]]")
    lines.append("")
    lines.append(f"## {labels['contributors']}")
    lines.append("")
    for author in contributors:
        lines.append(f"- {author}")
    lines.append("")

    index_path = repo_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    written.append(index_path)

    by_month = defaultdict(list)
    for c in changes:
        by_month[month_of(c)].append(c)
    timeline_dir.mkdir(parents=True, exist_ok=True)
    for ym, month_changes in by_month.items():
        page_lines = [marker, f"[[repos/{slug}/index|{labels['back']}]]", "", f"# {ym}", ""]
        month_summary = summaries.get("months", {}).get(ym)
        if month_summary:
            page_lines.append(f"## {labels['summary_heading']}")
            page_lines.append("")
            page_lines.append(month_summary)
            page_lines.append("")
        for c in month_changes:
            page_lines.append(_change_line(c, slug, area_slug_map))
        page_lines.append("")
        path = timeline_dir / f"{ym}.md"
        path.write_text("\n".join(page_lines), encoding="utf-8")
        written.append(path)

    by_area = defaultdict(list)
    for c in changes:
        for a in c.areas:
            by_area[a].append(c)
    areas_dir.mkdir(parents=True, exist_ok=True)
    for a, area_changes in by_area.items():
        page_lines = [marker, f"[[repos/{slug}/index|{labels['back']}]]", "", f"# {a}", ""]
        area_summary = summaries.get("areas", {}).get(a)
        if area_summary:
            page_lines.append(f"## {labels['summary_heading']}")
            page_lines.append("")
            page_lines.append(area_summary)
            page_lines.append("")
        for c in area_changes:
            page_lines.append(_change_line(c, slug, area_slug_map))
        page_lines.append("")
        path = areas_dir / f"{area_slug_map[a]}.md"
        path.write_text("\n".join(page_lines), encoding="utf-8")
        written.append(path)

    add_index_links(wiki_path, labels["repositories_section"], [f"repos/{slug}/index|{repo}"])
    return written
