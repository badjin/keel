from __future__ import annotations

import re
from dataclasses import dataclass


class PlanError(ValueError):
    pass


@dataclass
class Phase:
    n: int
    title: str
    scenarios: list[str]
    tests: list[str]
    steps: list[tuple[int, bool]]
    start: int
    end: int


_FRONT_MATTER = re.compile(r"\A---\r?\nkeel_plan: 1\r?\n---(?:\r?\n|\Z)")
_PHASE = re.compile(r"^## Phase (\d+): (.+)$")
_STEP = re.compile(r"^\s*- \[([ x])\]")
_RUN = re.compile(r"^\s*- Run: `([^`]+)`\s*$")
_OUTWARD = re.compile(
    r"git push|git commit|gh pr create|gh pr merge|gh release|npm publish|"
    r"vercel|netlify deploy|twine upload|docker push|"
    r"git switch\b|git checkout\s+(?:-[bB]|--orphan)\b|git branch\s+(?:-[cC]\s|[A-Za-z0-9_])|"
    r"git worktree add\b|gh pr checkout\b"
)
_CHECKOUT_REF = re.compile(r"git checkout\s+[A-Za-z0-9_]")


def parse(text: str) -> list[Phase]:
    if not _FRONT_MATTER.match(text):
        raise PlanError("missing front matter keel_plan: 1")

    lines = text.splitlines()
    phases: list[Phase] = []
    fenced = False
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        if line.startswith("## "):
            headings.append((index, line))
        match = _PHASE.fullmatch(line)
        if not match:
            continue
        number = int(match.group(1))
        if number != len(phases) + 1:
            raise PlanError(f"phase out of order: {number}")
        if phases:
            phases[-1].end = index
        phases.append(Phase(number, match.group(2), [], [], [], index, len(lines)))

    if phases:
        last = phases[-1]
        last.end = next((index for index, line in headings
                         if index > last.start and not _PHASE.fullmatch(line)), len(lines))

    for phase in phases:
        in_tests = False
        fenced = False
        for index in range(phase.start + 1, phase.end):
            line = lines[index]
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            if line.lower().startswith("### test") and line != "### Tests":
                raise PlanError(f"invalid tests heading at line {index + 1}")
            if line.startswith("## ") or line.startswith("### "):
                in_tests = line == "### Tests"
            if line.startswith("> Scenarios:"):
                phase.scenarios = [item.strip() for item in line[len("> Scenarios:"):].split(",") if item.strip()]
            if in_tests and line.lstrip().startswith("- Run:"):
                run = _RUN.fullmatch(line)
                if not run:
                    raise PlanError(f"invalid test command at line {index + 1}")
                phase.tests.append(run.group(1))
            step = _STEP.match(line)
            if step:
                phase.steps.append((index, step.group(1) == "x"))
    return phases


def outward_steps(text: str) -> list[str]:
    matches = []
    fence_language: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if fence_language is None:
                fence_language = stripped[3:].strip().split(" ", 1)[0]
            else:
                fence_language = None
            continue
        if fence_language == "text" or stripped.startswith(">"):
            continue
        if _OUTWARD.search(line) or (_CHECKOUT_REF.search(line) and " -- " not in line):
            matches.append(line)
    return matches


def tick_phase(text: str, n: int) -> str:
    phase = next((phase for phase in parse(text) if phase.n == n), None)
    if phase is None:
        raise PlanError(f"missing phase: {n}")
    lines = text.splitlines(keepends=True)
    for index, ticked in phase.steps:
        if not ticked:
            lines[index] = _STEP.sub(lambda match: match.group().replace("[ ]", "[x]"), lines[index], count=1)
    return "".join(lines)
