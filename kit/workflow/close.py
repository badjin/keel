from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import approval, docs, fsutil, plan, work


class CloseError(Exception):
    pass


def _record(folder: Path, kind: str) -> Path:
    stem = f"{kind}-{fsutil.stamp()}"
    path = folder / "records" / f"{stem}.md"
    suffix = 2
    while path.exists():
        path = folder / "records" / f"{stem}-{suffix}.md"
        suffix += 1
    return path


def _ids(items: list[str]) -> list[str]:
    ids = []
    for item in items:
        match = re.match(r"^(S[0-9]+)\b", item.replace("**", ""))
        if not match:
            raise CloseError(f"invalid approved scenario: {item}")
        ids.append(match.group(1))
    if len(ids) != len(set(ids)):
        raise CloseError("duplicate approved scenario ids")
    return ids


def _approved(state: dict) -> tuple[list[str], str | None]:
    spec = state.get("reviews", {}).get("spec", {})
    if "exempt" in spec:
        return [], spec["exempt"]
    return spec.get("scenarios", []), None


def _existing(folder: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else folder / path


def _header_pass(path: Path | None, field: str) -> bool:
    if not path or not path.is_file():
        return False
    header = re.split(r"(?m)^## ", path.read_text(encoding="utf-8"), maxsplit=1)[0]
    return bool(re.search(rf"(?m)^- {field}: PASS[ \t]*$", header))


def verify(folder: Path, results: dict | None, exempt: bool) -> Path:
    folder = Path(folder)
    state = work.load_state(folder)
    if state.get("reviews", {}).get("spec", {}).get("verdict") != "PASS":
        raise CloseError("spec review is not PASS")
    items, exemption = _approved(state)
    if exempt:
        if exemption is None:
            raise CloseError("spec review has no approved exemption")
        ids = []
        body = f"# Verification\n\n## Approved exemption\n{exemption}\n"
    else:
        if exemption is not None:
            raise CloseError("spec review requires exempt verification")
        ids = _ids(items)
        given = results if isinstance(results, dict) else {}
        missing = sorted(set(ids) - set(given))
        extra = sorted(set(given) - set(ids))
        failing = sorted(key for key in set(ids) & set(given)
                         if not isinstance(given[key], dict)
                         or given[key].get("result") != "PASS"
                         or not isinstance(given[key].get("evidence"), str)
                         or not given[key]["evidence"].strip())
        if missing or extra or failing:
            raise CloseError("; ".join(f"{name}: {', '.join(values)}" for name, values in
                                       (("missing", missing), ("extra", extra), ("failing", failing)) if values))
        body = "# Verification\n\n## Approved scenarios\n" + "".join(f"- {item}\n" for item in items)
        for scenario_id in ids:
            body += f"\n## {scenario_id}\n- Result: PASS\n- Evidence: {given[scenario_id]['evidence']}\n"
    at = fsutil.now_iso()
    record = _record(folder, "verification")
    fsutil.atomic_write_text(record, body)
    work.update_state(folder, lambda data: data.update(verification={
        "record": str(record), "ids": ids, "at": at,
    }))
    return record


def audit(keel_home: Path, folder: Path) -> tuple[bool, list[str], Path]:
    folder = Path(folder)
    state = work.load_state(folder)
    reviews = state.get("reviews", {})
    problems = []
    intent = (folder / "intent.md").read_text(encoding="utf-8")
    approved = approval.approved_hash(Path(keel_home), folder)
    if not approved or docs.get_status(intent) not in ("approved", "done"):
        problems.append("intent not approved in ledger")
    if (reviews.get("intent", {}).get("verdict") != "PASS" or
            reviews.get("intent", {}).get("hashes", {}).get("intent") != approved):
        problems.append("no PASS intent review on approved hash")
    for stage, hash_fn in (("spec", docs.spec_hash), ("plan", docs.plan_hash)):
        path = folder / f"{stage}.md"
        if not path.is_file():
            problems.append(f"{stage} missing")
            continue
        digest = hash_fn(path.read_text(encoding="utf-8"))
        reviewed = reviews.get(stage, {})
        if reviewed.get("verdict") != "PASS" or reviewed.get("hashes", {}).get(stage) != digest:
            problems.append(f"{stage} review not PASS on current {stage} hash")
    try:
        phases = plan.parse((folder / "plan.md").read_text(encoding="utf-8")) if (folder / "plan.md").is_file() else []
    except plan.PlanError as error:
        problems.append(str(error))
        phases = []
    run = state.get("run", {})
    repo = run.get("repo")
    for phase in phases:
        prefix = f"phase {phase.n}"
        current = run.get("phases", {}).get(str(phase.n), {})
        if not phase.steps or any(not ticked for _, ticked in phase.steps):
            problems.append(f"{prefix} not ticked")
        tests = _existing(folder, current.get("tests_record"))
        if not _header_pass(tests, "Overall"):
            problems.append(f"{prefix} has no passing tests record file")
        status = current.get("status")
        if status not in ("done", "no-change"):
            problems.append(f"{prefix} status is not done or no-change")
        if status == "no-change" and current.get("commit") != current.get("base"):
            problems.append(f"{prefix} marked no-change but has commits")
        if status == "done":
            review = _existing(folder, current.get("review_record"))
            if not _header_pass(review, "Verdict"):
                problems.append(f"{prefix} has no PASS review record file")
        sha = current.get("commit")
        if not repo or not sha or subprocess.run(
                ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                capture_output=True).returncode:
            problems.append(f"{prefix} commit missing")
    try:
        items, exemption = _approved(state)
        expected = [] if exemption is not None else _ids(items)
    except CloseError as error:
        problems.append(str(error))
        expected = None
    verification = state.get("verification", {})
    record_path = _existing(folder, verification.get("record"))
    if not record_path or not record_path.is_file():
        problems.append("no verification record")
    elif expected is not None and verification.get("ids") != expected:
        problems.append("verification ids differ from approved list")
    passed = not problems
    at = fsutil.now_iso()
    record = _record(folder, "audit")
    body = f"# Audit\n\n- Result: {'PASS' if passed else 'FAIL'}\n- At: {at}\n"
    if problems:
        body += "\n## Problems\n" + "".join(f"- {problem}\n" for problem in problems)
    fsutil.atomic_write_text(record, body)
    work.update_state(folder, lambda data: data.update(audit={
        "passed": passed, "problems": problems, "record": str(record), "at": at,
    }))
    if passed:
        updated = docs.set_status(intent, "done")
        updated = docs.add_changelog(updated, f"{at[:10]} audit passed")
        fsutil.atomic_write_text(folder / "intent.md", updated)
    return passed, problems, record


def archive(kb: Path, folder: Path) -> str:
    kb, folder = Path(kb), Path(folder)
    intent = (folder / "intent.md").read_text(encoding="utf-8")
    state = work.load_state(folder)
    if docs.get_status(intent) != "done" or not state.get("audit", {}).get("passed"):
        raise CloseError("work has not passed audit")
    lang = state.get("lang", "en")
    page = kb / "wiki" / "work-archive.md"
    original = page.read_text(encoding="utf-8") if page.is_file() else ""
    link = f"raw/work/{folder.name}/intent"
    if link in original:
        return "already archived"
    if not page.is_file():
        original = (f"{docs.text('archive_title', lang)}\n\n"
                    f"{docs.text('archive_intro', lang)}\n\n"
                    f"{docs.text('archive_header', lang)}\n")
    summary = " ".join(docs.sections(intent).get("Summary", "").split())
    first = re.split(r"(?<=[.!?])\s+", summary, maxsplit=1)[0].replace("|", r"\|")
    created = state.get("created", folder.name[:10])
    base = f"raw/work/{folder.name}"
    row = (f"| {created} | [[{base}/intent]] | [[{base}/spec]] | "
           f"[[{base}/plan]] | {first} |\n")
    fsutil.atomic_write_text(page, original + ("\n" if original and not original.endswith("\n") else "") + row)
    index = kb / "index.md"
    index_text = index.read_text(encoding="utf-8") if index.is_file() else ""
    entry = "- [[wiki/work-archive]]\n"
    if entry.strip() not in index_text.splitlines():
        heading = docs.text("topics_heading", lang)
        lines = index_text.splitlines(keepends=True)
        start = next((i for i, line in enumerate(lines) if line.rstrip("\r\n") == heading), None)
        if start is None:
            insertion = len(lines)
        else:
            end = next((i for i in range(start + 1, len(lines))
                        if re.match(r"^#{1,2} ", lines[i])), len(lines))
            insertion = end
            while insertion > start + 1 and not lines[insertion - 1].strip():
                insertion -= 1
        prefix = "\n" if insertion and not lines[insertion - 1].endswith("\n") else ""
        lines.insert(insertion, prefix + entry)
        fsutil.atomic_write_text(index, "".join(lines))
    return "archived"
