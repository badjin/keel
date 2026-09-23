"""Collects landed (mainline) changes from a repo's git history."""
from __future__ import annotations
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

RECORD_SEP = "\x1e"
UNIT_SEP = "\x1f"
BODY_END = "\x1d"

MERGE_PR_RE = re.compile(r"Merge pull request #(\d+)")
TRAILING_PR_RE = re.compile(r"\(#(\d+)\)\s*$")
TRAILING_PR_STRIP_RE = re.compile(r"\s*\(#\d+\)\s*$")


@dataclass
class Change:
    sha: str
    is_merge: bool
    author: str
    date: str
    subject: str
    title: str
    pr: Optional[int]
    areas: list


def _git_env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def _is_bare_repo(git_dir: Path, env: dict) -> bool:
    result = subprocess.run(
        ["git", "-C", str(git_dir), "rev-parse", "--is-bare-repository"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_base_args(git_dir: Path, env: dict) -> list:
    git_dir = Path(git_dir)
    if _is_bare_repo(git_dir, env):
        return ["git", "--git-dir", str(git_dir)]
    return ["git", "-C", str(git_dir)]


def _area_of(file_path: str) -> str:
    if "/" in file_path:
        return file_path.split("/", 1)[0]
    return "(root)"


def _parse_record(record: str) -> Optional[Change]:
    parts = record.split(UNIT_SEP, 5)
    if len(parts) < 6:
        return None
    sha, parents, author, date, subject, tail = parts

    is_merge = len(parents.split()) > 1

    pr = None
    merge_match = MERGE_PR_RE.search(subject)
    if merge_match:
        pr = int(merge_match.group(1))
    else:
        trailing_match = TRAILING_PR_RE.search(subject)
        if trailing_match:
            pr = int(trailing_match.group(1))

    body_part, _sep, rest = tail.partition(BODY_END)
    files = [line for line in rest.split("\n") if line.strip()]

    if subject.startswith("Merge pull request"):
        title = next((line.strip() for line in body_part.split("\n") if line.strip()), subject)
    else:
        title = TRAILING_PR_STRIP_RE.sub("", subject)

    areas = sorted({_area_of(f) for f in files})

    return Change(
        sha=sha,
        is_merge=is_merge,
        author=author,
        date=date,
        subject=subject,
        title=title,
        pr=pr,
        areas=areas,
    )


def _parse(output: str) -> list:
    changes = []
    for record in output.split(RECORD_SEP):
        if not record.strip():
            continue
        change = _parse_record(record)
        if change is not None:
            changes.append(change)
    return changes


def _committer_date(date_str: str) -> datetime:
    if date_str.endswith("Z"):
        date_str = date_str[:-1] + "+00:00"
    return datetime.fromisoformat(date_str).astimezone()


def collect_changes(git_dir: Path, branch: str, since_days: int, now: Optional[datetime] = None) -> list:
    now = (now or datetime.now()).astimezone()
    since_dt = now - timedelta(days=since_days)
    prefilter_dt = since_dt - timedelta(days=1)

    env = _git_env()
    args = _git_base_args(git_dir, env) + [
        "log",
        branch,
        "--first-parent",
        "--no-renames",
        "--diff-merges=first-parent",
        f"--since-as-filter={prefilter_dt.isoformat()}",
        f"--format={RECORD_SEP}%H{UNIT_SEP}%P{UNIT_SEP}%an{UNIT_SEP}%cI{UNIT_SEP}%s{UNIT_SEP}%b{BODY_END}",
        "--name-only",
        "--",
    ]
    result = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    changes = _parse(result.stdout)
    return [c for c in changes if _committer_date(c.date) >= since_dt]


def month_of(c: Change) -> str:
    return c.date[:7]
