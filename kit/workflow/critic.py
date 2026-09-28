from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from . import approval, docs, fsutil, transcripts, work
from .plan import outward_steps


class Refused(Exception):
    pass


class ReviewError(Exception):
    pass


EXPECTED_CHECKS = {
    "intent": ["outcome-traced", "no-omission", "uncertainty-declared", "invariant"],
    "spec": ["follows-intent", "scenarios-observable", "scenarios-cover-outcomes", "failure-cases", "no-duplicates"],
    "plan": ["follows-spec", "scenarios-covered", "no-outward-steps", "test-budget"],
    "phase": ["requirement-met", "every-line-traced", "no-overengineering", "orphans-cleaned", "style-matched"],
    "light": ["requirement-met", "every-line-traced", "no-overengineering", "orphans-cleaned", "style-matched"],
}


def child_env(base: dict) -> dict:
    env = {key: value for key, value in base.items()
           if key != "CLAUDECODE" and not key.startswith("CLAUDE_CODE_")}
    env["LLM_WIKI_KIT_NESTED"] = "1"
    return env


def build_cmd(cli: str, exe: str, cwd: str) -> list[str]:
    if cli == "claude":
        return [exe, "-p", "--setting-sources", "project,local", "--disallowedTools",
                "Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"]
    if cli == "codex":
        return [exe, "exec", "-C", cwd, "-s", "read-only", "-c",
                "features.hooks=false", "--skip-git-repo-check"]
    raise ValueError(cli)


def parse_answer(answer: str, stage: str) -> tuple[str, list[str]]:
    verdicts = re.findall(r"^VERDICT:\s*(PASS|FAIL)\s*$", answer, re.MULTILINE)
    checks = [line for line in answer.splitlines()
              if re.match(r"^CHECK\s+[\w-]+:\s*(PASS|FAIL)\b", line)]
    results = {match.group(1): match.group(2) for line in checks
               if (match := re.match(r"^CHECK\s+([\w-]+):\s*(PASS|FAIL)\b", line))}
    expected = EXPECTED_CHECKS[stage]
    if not verdicts or any(name not in results for name in expected):
        verdict = "ERROR"
    elif any(results[name] == "FAIL" for name in expected):
        verdict = "FAIL"
    else:
        verdict = verdicts[-1]
    return verdict, checks


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode:
        raise ReviewError(result.stderr.strip() or "git command failed")
    return result.stdout


def _changed_files(repo: Path, base: str, *, light: bool) -> tuple[str, str]:
    if light:
        names = _git(repo, "-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff",
                     "--name-only", base).splitlines()
        names += _git(repo, "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard").splitlines()
    else:
        names = _git(repo, "-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff",
                     "--name-only", f"{base}..HEAD").splitlines()
    texts = []
    total = 0
    for name in dict.fromkeys(names):
        path = repo / name
        if not path.is_file() or path.is_symlink():
            continue
        try:
            content = path.read_text(encoding="utf-8")[:20000]
        except (OSError, UnicodeError):
            continue
        if total >= 120000:
            break
        content = content[:120000 - total]
        total += len(content)
        texts.append(f"--- {name} ---\n{content}")
    return "\n".join(names), "\n".join(texts)


def _phase_text(plan: str, phase: int) -> str:
    match = re.search(rf"(?m)^## Phase {phase}:.*$", plan)
    if not match:
        raise Refused(f"phase {phase} not found in plan")
    next_phase = re.search(r"(?m)^## Phase \d+:.*$", plan[match.end():])
    end = match.end() + next_phase.start() if next_phase else len(plan)
    return plan[match.start():end]


def _block(name: str, body: str) -> str:
    return f"\n=== BEGIN {name} ===\n{body.rstrip()}\n=== END {name} ===\n"


def run_review(stage: str, *, keel_home: Path, kb: Path, folder: Path | None,
               repo: Path | None = None, phase: int | None = None, request: str = "",
               base: str = "", statements: Path | None = None, extra_review: bool = False,
               tests_record: Path | None = None,
               run=subprocess.run) -> tuple[str, Path | None, list[str], str]:
    if stage not in ("intent", "spec", "plan", "phase", "light"):
        raise ValueError(stage)
    if stage != "light" and folder is None:
        raise ValueError("folder required")
    if stage in ("phase", "light") and repo is None:
        raise ValueError("repo required")
    state = work.load_state(folder) if folder else {}
    key = f"phase-{phase}" if stage == "phase" else stage
    if stage == "phase" and (phase is None or phase < 1):
        raise ValueError("positive phase required")
    if stage in ("spec", "plan") and not approval.is_approved(keel_home, folder):
        raise Refused(f"intent not approved by the user — ask them to type: approve {state.get('slug', folder.name)}")
    if stage == "intent" and state.get("review_fails", {}).get("intent", 0) >= 3 and not extra_review:
        raise Refused("intent review limit reached")
    if stage == "phase" and state.get("review_fails", {}).get(key, 0) >= 2 and not extra_review:
        raise Refused("phase review limit reached")

    inputs = []
    hashes = {}
    if stage in ("intent", "spec"):
        intent = (folder / "intent.md").read_text(encoding="utf-8")
        inputs.append(("intent.md", intent))
        hashes["intent"] = docs.intent_hash(intent)
    if stage in ("spec", "plan", "phase"):
        spec = (folder / "spec.md").read_text(encoding="utf-8")
        hashes["spec"] = docs.spec_hash(spec)
        if stage != "phase":
            inputs.append(("spec.md", spec))
    if stage == "plan":
        reviewed = state.get("reviews", {}).get("spec", {})
        if reviewed.get("verdict") != "PASS" or reviewed.get("hashes", {}).get("spec") != hashes["spec"]:
            raise Refused("spec has no PASS review on its current hash")
    outward = []
    if stage in ("plan", "phase"):
        plan = (folder / "plan.md").read_text(encoding="utf-8")
        if stage == "plan":
            inputs.append(("plan.md", plan))
            hashes["plan"] = docs.plan_hash(plan)
            outward = outward_steps(plan)
    if stage == "intent":
        session_paths = [Path(session.get("transcript_path", "")) for session in state.get("sessions", [])]
        readable = []
        for session_path in session_paths:
            try:
                if session_path.is_file():
                    session_path.read_text(encoding="utf-8")
                    readable.append(session_path)
            except (OSError, UnicodeError):
                continue
        if not readable:
            raise Refused("the intent review needs this session's transcript to check the knowledge base reads (D18); there is no bypass")
        if statements is not None:
            messages = statements.read_text(encoding="utf-8")
        else:
            messages = "\n".join(
                (f"Agent asked: {agent[-1500:]}\n" if agent else "") + f"User: {message}"
                for session_path in readable
                for message, agent in transcripts.user_messages_with_context(str(session_path)))
            messages = messages[-40000:]
        if not messages.strip():
            raise Refused("no user messages found")
        inputs.append(("user messages", messages))
        pages = docs.kb_pages(intent)
        kb_problems = []
        if not pages:
            kb_problems.append("no knowledge base pages listed")
        kb_root = kb.resolve()
        work_root = folder.resolve()
        work_paths = {unicodedata.normalize("NFC", str(work_path))
                      for work_path in (folder.absolute(), work_root)}
        work_paths.update(unicodedata.normalize("NFC", "~/" + str(work_path.relative_to(keel_home.parent)))
                          for work_path in (folder.absolute(), work_root)
                          if work_path.is_relative_to(keel_home.parent))
        tool_texts = [text for session_path in readable
                      for text in transcripts.tool_call_texts(str(session_path))
                      if not any(work_path in variant
                                 for variant in transcripts.path_variants(text, str(keel_home.parent))
                                 for work_path in work_paths)]
        for page in pages:
            listed_path = Path(page) if Path(page).is_absolute() else kb / page
            page_path = listed_path.resolve()
            if page_path.suffix != ".md" or not page_path.is_file() or not page_path.is_relative_to(kb_root) or page_path.is_relative_to(work_root):
                kb_problems.append(f"invalid knowledge base page: {page}")
            elif not (transcripts.read_evidence(tool_texts, str(listed_path), str(keel_home.parent))
                      or transcripts.read_evidence(tool_texts, str(page_path), str(keel_home.parent))):
                kb_problems.append(f"unread knowledge base page: {page}")
    if stage == "phase":
        phase_base = state.get("run", {}).get("phases", {}).get(str(phase), {}).get("base")
        if not phase_base:
            raise Refused("phase base not recorded")
        design = docs.sections(spec).get("Design", "")
        title = next((line for line in spec.splitlines() if line.startswith("# ")), "")
        inputs.extend([("spec goal and design", title + "\n## Design\n" + design),
                       (f"phase {phase} plan", _phase_text(plan, phase))])
        head = _git(repo, "rev-parse", "HEAD").strip()
        diff = _git(repo, "diff", "--no-color", "--no-ext-diff", f"{phase_base}..HEAD")
        hashes["phase"] = f"{phase_base}..{head}"
        _, changed_text = _changed_files(repo, phase_base, light=False)
        selected = tests_record
        if selected is None:
            records = sorted((folder / "records").glob(f"phase-{phase}-tests-*.md"))
            selected = records[-1] if records else None
        inputs.extend([("git diff", diff), ("changed files", changed_text),
                       ("phase tests", selected.read_text(encoding="utf-8") if selected else "")])
    if stage == "light":
        effective_base = base or "HEAD"
        diff = _git(repo, "diff", "--no-color", "--no-ext-diff", effective_base)
        names, changed_text = _changed_files(repo, effective_base, light=True)
        inputs.extend([("request", request), ("git diff", diff),
                       ("untracked and changed paths", names), ("changed files", changed_text)])

    config = fsutil.read_json(keel_home / "config.json", {})
    workflow = config.get("workflow", {})
    cli = workflow.get("critic_cli", "")
    exe = shutil.which(cli) if cli in ("claude", "codex") else None
    language = state.get("lang", workflow.get("language", "en"))
    prompt_file = Path(__file__).with_name("critic") / f"{stage}.{language}.md"
    if not prompt_file.is_file():
        prompt_file = Path(__file__).with_name("critic") / f"{stage}.en.md"
    prompt = "KEEL-CRITIC\n" + prompt_file.read_text(encoding="utf-8")
    prompt += "".join(_block(name, body) for name, body in inputs)
    answer = ""
    if stage == "intent" and kb_problems:
        verdict = "FAIL"
        checks = ["CHECK kb-consulted: FAIL — " + "; ".join(kb_problems)]
    elif stage == "plan" and outward:
        verdict = "FAIL"
        checks = ["CHECK no-outward-steps: FAIL — " + "; ".join(line.strip() for line in outward)]
    elif not exe:
        verdict = "ERROR"
        answer = f"Review CLI unavailable: {cli or 'not configured'}"
        checks = []
    else:
        with tempfile.TemporaryDirectory(prefix="keel-critic-") as tmpdir:
            cmd = build_cmd(cli, exe, tmpdir)
            try:
                result = run(cmd, cwd=tmpdir, env=child_env(os.environ), input=prompt,
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=float(os.environ.get("KEEL_WF_CRITIC_TIMEOUT", "900")))
                answer = result.stdout or ""
                if result.returncode:
                    verdict, checks = "ERROR", []
                    answer += "\n" + (result.stderr or f"CLI exited {result.returncode}")
                else:
                    verdict, checks = parse_answer(answer, stage)
            except (subprocess.TimeoutExpired, OSError) as error:
                verdict, checks, answer = "ERROR", [], str(error)
    if stage == "intent" and not kb_problems:
        checks.append(f"CHECK kb-consulted: PASS — {len(pages)} pages read")

    at = fsutil.now_iso()
    if stage == "light":
        log_request = request.replace("\r", " ").replace("\n", " ")
        fsutil.append_line(keel_home / "state" / "workflow" / "light.log",
                           f"{at} {cli or 'none'} {verdict} {repo} {log_request}")
        return verdict, None, checks, answer
    record = folder / "records" / f"critic-{key}-{fsutil.stamp()}.md"
    if record.exists():
        stem = record.stem
        suffix = 2
        while record.exists():
            record = record.with_name(f"{stem}-{suffix}.md")
            suffix += 1
    reviewed = ", ".join(f"{name} {digest if name == 'phase' else 'sha256:' + digest}"
                         for name, digest in hashes.items())
    body = (f"# Keel review record\n- Stage: {key}\n- CLI: {cli or 'none'}\n"
            f"- Verdict: {verdict}\n- Recorded at: {at}\n- Reviewed: {reviewed}\n\n"
            "## Checks\n" + "\n".join(checks) + "\n")
    scenarios = docs.spec_scenarios(spec) if stage == "spec" and verdict == "PASS" else None
    if scenarios is not None:
        body += "\n## Approved scenarios\n"
        if "exempt" in scenarios:
            body += f"- Exempt: {scenarios['exempt']}\n"
        else:
            body += "".join(f"- {item}\n" for item in scenarios["scenarios"])
    if not ((stage == "intent" and kb_problems) or (stage == "plan" and outward)):
        body += "\n## Answer\n" + answer.rstrip() + "\n"
    fsutil.atomic_write_text(record, body)

    def save(data: dict) -> None:
        entry = {"verdict": verdict, "hashes": hashes, "record": str(record), "at": at}
        if scenarios is not None:
            entry.update(scenarios)
        data.setdefault("reviews", {})[key] = entry
        if verdict == "FAIL":
            fails = data.setdefault("review_fails", {})
            fails[key] = fails.get(key, 0) + 1

    work.update_state(folder, save)
    if stage == "spec" and verdict == "PASS":
        current_spec = (folder / "spec.md").read_text(encoding="utf-8")
        if docs.spec_hash(current_spec) != hashes["spec"]:
            raise ReviewError("spec changed during the review")
        pointer = f"- Scenario review: records/{record.name}"
        if re.search(r"(?m)^- Scenario review:.*$", current_spec):
            updated = re.sub(r"(?m)^- Scenario review:.*$", pointer, current_spec, count=1)
        else:
            updated = re.sub(r"(?m)^(- Intent:.*)$", r"\1\n" + pointer, current_spec, count=1)
            if updated == current_spec:
                first, _, rest = current_spec.partition("\n")
                updated = first + "\n" + pointer + "\n" + rest
        fsutil.atomic_write_text(folder / "spec.md", updated)
    return verdict, record, checks, answer
