"""Optional LLM summary pass over collected changes, run through an installed CLI."""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from typing import Callable, Optional

from kit.history import Change, month_of

MAX_LINES = 200
CLI_NAMES = ("claude", "codex")

_INSTRUCTION = {
    "ko": "한국어로 3~5문장, 제목 없이 평문으로, 아래 나열된 항목만 근거로 설명하세요. 목록에 없는 내용은 추측하지 마세요.",
    "en": "Write 3-5 sentences in English, plain prose with no headings, describing only what is listed below. Do not guess beyond these entries.",
}


def available_clis() -> list:
    return [name for name in CLI_NAMES if shutil.which(name)]


def _change_line(c: Change) -> str:
    pr = c.pr if c.pr is not None else ""
    return f"{c.date} | {pr} | {c.title} | {c.author} | {','.join(c.areas)}"


def _prompt(repo: str, scope: str, changes: list, language: str) -> str:
    instruction = _INSTRUCTION["en" if language == "en" else "ko"]
    lines = [_change_line(c) for c in changes[:MAX_LINES]]
    body = "\n".join(lines)
    return f"{instruction}\n\nRepo: {repo}\nScope: {scope}\n\n{body}"


def build_prompts(repo: str, changes: list, language: str) -> dict:
    lang = language if language in ("ko", "en") else "ko"

    by_month = defaultdict(list)
    for c in changes:
        by_month[month_of(c)].append(c)

    area_counter = Counter()
    by_area = defaultdict(list)
    for c in changes:
        for a in c.areas:
            area_counter[a] += 1
            by_area[a].append(c)
    top_areas = [a for a, _ in area_counter.most_common(10)]

    return {
        "index": _prompt(repo, "all", changes, lang),
        "months": {ym: _prompt(repo, ym, month_changes, lang) for ym, month_changes in by_month.items()},
        "areas": {a: _prompt(repo, a, by_area[a], lang) for a in top_areas},
    }


def run_cli(cli: str, prompt: str, timeout: int = 120, env: Optional[dict] = None) -> Optional[str]:
    if cli == "claude":
        args = ["claude", "-p", prompt]
    elif cli == "codex":
        args = ["codex", "exec", "--skip-git-repo-check", prompt]
    else:
        return None

    env = {**(env if env is not None else os.environ), "LLM_WIKI_KIT_SUPPRESS": "1"}

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            result = subprocess.run(
                args,
                cwd=tmp_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                check=False,
                env=env,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None

    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def summarize(
    cli: str,
    prompts: dict,
    runner: Callable = run_cli,
    progress: Optional[Callable] = None,
    env: Optional[dict] = None,
) -> dict:
    def _report(msg: str) -> None:
        if progress:
            progress(msg)

    # `env` is only forwarded when the caller supplies one (the worker's
    # refresh job, running in _child_env()) — omitting the kwarg entirely
    # otherwise keeps a bare two-arg runner (e.g. a test fake) working.
    def _run(prompt: str):
        if env is not None:
            return runner(cli, prompt, env=env)
        return runner(cli, prompt)

    result: dict = {"months": {}, "areas": {}}

    _report("index")
    text = _run(prompts["index"])
    if text:
        result["index"] = text

    for ym, prompt in prompts.get("months", {}).items():
        _report(ym)
        text = _run(prompt)
        if text:
            result["months"][ym] = text

    for area, prompt in prompts.get("areas", {}).items():
        _report(area)
        text = _run(prompt)
        if text:
            result["areas"][area] = text

    return result
