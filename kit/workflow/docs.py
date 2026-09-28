from __future__ import annotations

import hashlib
import json
import re
import string
from pathlib import Path


REQUIRED_INTENT_SECTIONS = (
    "Summary", "Problem", "Proposed outcome", "Affected users and systems",
    "Knowledge base consulted", "Constraints", "Decisions", "Open questions", "Quotes", "Status", "Changelog",
)
REFERENCE = re.compile(r"\b[QD][0-9]+\b")
KB_BULLET = re.compile(r"^\s*-\s+(?:`([^`]+)`|\[\[([^\]|]+)(?:\|[^\]]+)?\]\])")


def _section_spans(text: str) -> list[tuple[str, int, int, int]]:
    lines = text.splitlines(keepends=True)
    spans = []
    offset = 0
    fenced = False
    for line in lines:
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            spans.append((line[3:].rstrip("\r\n"), offset, offset + len(line), len(text)))
        offset += len(line)
    return [(name, start, body, spans[index + 1][1] if index + 1 < len(spans) else len(text))
            for index, (name, start, body, _) in enumerate(spans)]


def sections(text: str) -> dict[str, str]:
    clean = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return {name: clean[body:end] for name, _, body, end in _section_spans(clean)}


def strip_sections(text: str, names: set[str]) -> str:
    result = []
    previous = 0
    for name, start, _, end in _section_spans(text):
        if name in names:
            result.append(text[previous:start])
            previous = end
    result.append(text[previous:])
    return "".join(result)


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def doc_hash(text: str) -> str:
    return hashlib.sha256(_lf(text).encode("utf-8")).hexdigest()


def intent_hash(text: str) -> str:
    normalized = "\n".join(line.rstrip() for line in _lf(text).split("\n"))
    return doc_hash(strip_sections(normalized, {"Status", "Changelog"}))


def spec_hash(text: str) -> str:
    lines = _lf(text).splitlines(keepends=True)
    return doc_hash("".join(line for line in lines if not line.startswith("- Scenario review:")))


def plan_hash(text: str) -> str:
    return doc_hash(re.sub(r"(?m)^(\s*- )\[[xX]\]", r"\1[ ]", _lf(text)))


def get_status(text: str) -> str:
    for line in sections(text).get("Status", "").splitlines():
        if line.strip():
            return line.strip().strip(string.punctuation + " ").lower()
    return ""


def set_status(text: str, status: str) -> str:
    for name, _, body, end in _section_spans(text):
        if name == "Status":
            content = text[body:end]
            match = re.search(r"(?m)^.*\S.*$", content)
            if match:
                content = content[:match.start()] + f"`{status}`" + content[match.end():]
            else:
                content = f"`{status}`\n" + content
            return text[:body] + content + text[end:]
    return text


def add_changelog(text: str, line: str) -> str:
    bullet = f"- {line}\n"
    for name, _, body, end in _section_spans(text):
        if name == "Changelog":
            content = text[body:end].rstrip("\n")
            return text[:body] + content + ("\n" if content else "") + bullet + text[end:]
    return text.rstrip("\n") + "\n\n## Changelog\n" + bullet


def kb_pages(text: str) -> list[str]:
    pages = []
    for line in sections(text).get("Knowledge base consulted", "").splitlines():
        match = KB_BULLET.match(line)
        if match:
            path = match.group(1)
            if path is None:
                path = match.group(2).split("#", 1)[0]
                if not path.endswith(".md"):
                    path += ".md"
            pages.append(path)
    return pages


def check_intent(text: str, none_tokens: list[str]) -> list[str]:
    parts = sections(text)
    problems = [f"missing section: {name}" for name in REQUIRED_INTENT_SECTIONS if name not in parts]
    problems.extend(f"empty section: {name}" for name in
                    ("Summary", "Problem", "Proposed outcome", "Constraints")
                    if name in parts and not parts[name].strip())
    if "Knowledge base consulted" in parts and not kb_pages(text):
        problems.append("no parseable knowledge base bullet")
    if "Open questions" in parts and parts["Open questions"].strip() not in ("", *none_tokens):
        problems.append("open questions remain")
    for name in ("Proposed outcome", "Constraints"):
        for line in parts.get(name, "").splitlines():
            if line.startswith("- ") and not any(REFERENCE.search(group) for group in re.findall(r"\[([^]]+)\]", line)):
                problems.append(f"bullet without [Q#]/[D#]: {line[:60]}")
    for reference in dict.fromkeys(REFERENCE.findall(" ".join(re.findall(r"\[([^]]+)\]", text)))):
        target = "Quotes" if reference.startswith("Q") else "Decisions"
        if not re.search(r"\*\*" + re.escape(reference) + r"\*\*", parts.get(target, "")):
            problems.append(f"unknown reference: {reference}")
    return problems


def spec_scenarios(text: str) -> dict:
    body = sections(text).get("Acceptance Scenarios", "")
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    if first.startswith("Exempt:"):
        return {"exempt": first[len("Exempt:"):].strip()}
    scenarios = []
    in_scenario = False
    for line in body.splitlines():
        if re.match(r"^- (?:\*\*)?S[0-9]+\b", line):
            scenarios.append(line[2:].strip())
            in_scenario = True
        elif not line or not line[:1].isspace():
            in_scenario = False
        elif in_scenario and line.strip():
            scenarios[-1] += " " + line.strip()
    return {"scenarios": scenarios}


def text(key: str, lang: str, **kw) -> str | list:
    catalogue = json.loads(Path(__file__).with_name("text.json").read_text(encoding="utf-8"))
    value = catalogue[key].get(lang, catalogue[key]["en"])
    return value.format(**kw) if isinstance(value, str) else value
