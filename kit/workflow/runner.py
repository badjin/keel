from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import approval, critic, docs, fsutil, plan, work


class RunError(Exception):
    pass


_SECRET = re.compile(r"(^|/)\.env($|\.)|\.pem$|\.key$|(^|/)id_rsa")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode:
        raise RunError(result.stderr.strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def _plan(folder: Path) -> tuple[str, list[plan.Phase]]:
    text = (folder / "plan.md").read_text(encoding="utf-8")
    try:
        return text, plan.parse(text)
    except plan.PlanError as error:
        raise RunError(str(error)) from error


def start(keel_home: Path, kb: Path, folder: Path, repo: Path) -> dict:
    state = work.load_state(folder)
    if "run" in state:
        raise RunError("run already started — use run status")
    if not approval.is_approved(keel_home, folder):
        raise RunError("intent is not approved")
    reviews = state.get("reviews", {})
    for stage, hash_fn in (("spec", docs.spec_hash), ("plan", docs.plan_hash)):
        current = hash_fn((folder / f"{stage}.md").read_text(encoding="utf-8"))
        reviewed = reviews.get(stage, {})
        if reviewed.get("verdict") != "PASS" or reviewed.get("hashes", {}).get(stage) != current:
            raise RunError(f"{stage} has no PASS review on its current hash")
    plan_text, _ = _plan(folder)
    outward = plan.outward_steps(plan_text)
    if outward:
        raise RunError("plan contains outward steps:\n" + "\n".join(outward))
    repo = Path(repo).resolve()
    top = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve()
    if Path(kb).resolve().is_relative_to(top):
        raise RunError("wiki path is inside git work tree")
    if _git(repo, "status", "--porcelain"):
        raise RunError("git work tree is not clean")
    branch = _git(repo, "symbolic-ref", "--short", "HEAD")
    base = _git(repo, "rev-parse", "HEAD")
    if branch in ("main", "master"):
        branch = f"keel/{state['slug']}"
        if subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet",
                           f"refs/heads/{branch}"], capture_output=True).returncode == 0:
            raise RunError(f"branch already exists: {branch}")
        _git(repo, "switch", "-c", branch)
    run = {"repo": str(top), "branch": branch, "base": base, "phases": {}}
    work.update_state(folder, lambda data: data.update(run=run))
    return run


def _tests_record(folder: Path, n: int, results: list[tuple[str, int, str]]) -> Path:
    record = folder / "records" / f"phase-{n}-tests-{fsutil.stamp()}.md"
    suffix = 2
    while record.exists():
        record = record.with_name(f"phase-{n}-tests-{fsutil.stamp()}-{suffix}.md")
        suffix += 1
    passed = all(code == 0 for _, code, _ in results)
    body = f"# Phase {n} tests\n\n- Overall: {'PASS' if passed else 'FAIL'}\n"
    for command, code, output in results:
        body += f"\n## Command\n\n`{command}`\n\n- Exit code: {code}\n\n```text\n{output[-4000:]}\n```\n"
    fsutil.atomic_write_text(record, body)
    return record


def run_phase(keel_home: Path, kb: Path, folder: Path, n: int, extra_review=False,
              review=critic.run_review) -> dict:
    state = work.load_state(folder)
    run = state.get("run")
    if not run:
        raise RunError("run has not started")
    plan_text, phases = _plan(folder)
    phase = next((item for item in phases if item.n == n), None)
    if phase is None:
        raise RunError(f"phase {n} not found")
    phase_states = run["phases"]
    if phase_states.get(str(n), {}).get("status") in ("done", "no-change"):
        raise RunError(f"phase {n} is already complete")
    if any(str(later) in phase_states for later in range(n + 1, len(phases) + 1)):
        raise RunError(f"phase {n} cannot run after a later phase has state")
    for previous in range(1, n):
        if phase_states.get(str(previous), {}).get("status") not in ("done", "no-change"):
            raise RunError(f"phase {previous} is not done")
    repo = Path(run["repo"])
    if _git(repo, "symbolic-ref", "--short", "HEAD") != run["branch"]:
        raise RunError("current branch does not match run branch")
    prior = phase_states.get(str(n - 1), {})
    base = phase_states.get(str(n), {}).get("base") or (prior.get("commit") if n > 1 else run["base"])
    if not base:
        raise RunError("previous phase commit is missing")

    def save_phase(fields: dict) -> None:
        def update(data: dict) -> None:
            data["run"]["phases"][str(n)] = fields
        work.update_state(folder, update)

    save_phase({**phase_states.get(str(n), {}), "base": base})
    results = []
    timeout = float(os.environ.get("KEEL_WF_TEST_TIMEOUT", "1800"))
    for command in phase.tests:
        try:
            result = subprocess.run(command, shell=True, cwd=repo, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=timeout)
            code = result.returncode
            output = (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired as error:
            code = 124
            output = str(error)
        results.append((command, code, output))
    tests_record = _tests_record(folder, n, results)
    if any(code != 0 for _, code, _ in results):
        fields = {"status": "tests-failed", "base": base, "tests_record": str(tests_record)}
        save_phase(fields)
        return fields

    _git(repo, "add", "-A")
    staged = _git(repo, "-c", "core.quotePath=false", "diff", "--cached", "--name-only", "--diff-filter=d").splitlines()
    committed = _git(repo, "-c", "core.quotePath=false", "diff", "--name-only", "--diff-filter=d",
                     f"{base}..HEAD").splitlines()
    secrets = sorted({name for name in staged + committed if _SECRET.search(name)})
    if secrets:
        _git(repo, "reset", "-q")
        raise RunError("refusing to commit secret-looking files: " + ", ".join(secrets))
    commit = _git(repo, "rev-parse", "HEAD")
    if not staged and commit == base:
        fields = {"status": "no-change", "base": base, "commit": commit,
                  "tests_record": str(tests_record)}
        fsutil.atomic_write_text(folder / "plan.md", plan.tick_phase(_plan(folder)[0], n))
        save_phase(fields)
        return fields
    if staged:
        result = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                                 f"keel: phase {n} — {phase.title}"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        if result.returncode:
            raise RunError("commit failed: " + result.stderr[-4000:].strip())
        commit = _git(repo, "rev-parse", "HEAD")
    save_phase({"base": base, "commit": commit})
    try:
        verdict, record, _, _ = review("phase", keel_home=keel_home, kb=kb, folder=folder,
                                        repo=repo, phase=n, extra_review=extra_review,
                                        tests_record=tests_record)
    except (critic.Refused, critic.ReviewError) as error:
        raise RunError(str(error)) from error
    fields = {"status": "done" if verdict == "PASS" else "review-failed",
              "base": base, "commit": commit, "tests_record": str(tests_record),
              "review_record": str(record) if record else None}
    if verdict == "PASS":
        fsutil.atomic_write_text(folder / "plan.md", plan.tick_phase(_plan(folder)[0], n))
    save_phase(fields)
    return fields


def status(keel_home: Path, kb: Path, folder: Path) -> str:
    state = work.load_state(folder)
    reviews = state.get("reviews", {})
    run = state.get("run", {})
    lines = [f"Approval: {'approved' if approval.is_approved(keel_home, folder) else 'pending'}",
             f"Spec review: {reviews.get('spec', {}).get('verdict', 'missing')}",
             f"Plan review: {reviews.get('plan', {}).get('verdict', 'missing')}",
             f"Run branch: {run.get('branch', 'not started')}"]
    _, phases = _plan(folder)
    for phase in phases:
        current = run.get("phases", {}).get(str(phase.n), {})
        lines.append(f"Phase {phase.n}: {current.get('status', 'pending')}")
    return "\n".join(lines)
