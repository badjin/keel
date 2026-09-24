#!/usr/bin/env python3
"""Two scheduling rules, called from the two existing kit hooks.

`check()` (called from wiki_loader.py's SessionStart hook) decides whether
it is time to check the watched repos for new merges, and `check_health()`
(called from wiki_auto_update.py's SessionEnd hook) decides whether 30
days have passed since the last monthly health check. Either one enqueues
a job (`{"kind": "refresh", ...}` or `{"kind": "health", ...}`) and starts
the detached worker when its rule is due. This module never fetches or
writes wiki pages itself, and never runs a CLI; every network call, every
CLI run, and every write to the wiki lives in _auto_update_worker.py under
its existing lock, so a refresh job, a health job, and a session-end job
never touch the wiki at the same time.

`maintenance.json` is worker-owned: these hooks only create it when it
does not exist yet (so the worker has a baseline to read on its very first
run), and never rewrite an existing one themselves. If it exists but is
unreadable or partial, both hooks log one line and do nothing else,
rather than risk clobbering state the worker is mid-write on.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common

CHECK_INTERVAL_SECONDS = 6 * 3600
HEALTH_INTERVAL_SECONDS = 30 * 24 * 3600

_EPOCH_ISO = "1970-01-01T00:00:00+00:00"


def _default_maintenance_state() -> dict:
    # `last_merge_check` starts at the epoch so a repo just added to
    # `watch_repos` is checked on the very next SessionStart, not up to 6
    # hours later. `last_health` starts at "now" instead — a brand-new
    # install has nothing worth health-checking yet, so the 30-day clock
    # starts from creation, not from the epoch.
    return {
        "last_merge_check": _EPOCH_ISO,
        "last_health": datetime.now(timezone.utc).isoformat(),
        "shas": {},
    }


def _create_state_if_missing(state_path: Path) -> None:
    """Creates `maintenance.json` with a baseline state only if it does not
    already exist, via O_CREAT | O_EXCL so two concurrent hook invocations
    never both "win" the create and clobber each other."""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(state_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(_default_maintenance_state()))
    except OSError:
        pass


def _read_state(state_path: Path):
    """Returns the parsed state dict, or None if it is missing, unreadable
    or not a dict — callers must treat None as "do nothing"."""
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _iso_dt(ts) -> "datetime | None":
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _last_merge_check_dt(state: dict):
    return _iso_dt(state.get("last_merge_check"))


def _last_health_dt(state: dict):
    return _iso_dt(state.get("last_health"))


def _due(last_dt, interval_seconds: int) -> bool:
    if last_dt is None:
        return True
    now = datetime.now(timezone.utc) if last_dt.tzinfo else datetime.now()
    return (now - last_dt).total_seconds() >= interval_seconds


def _has_pending_job(queue_dir: Path, kind: str) -> bool:
    if not queue_dir.exists():
        return False
    for job_path in queue_dir.glob("*.json"):
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(job, dict) and job.get("kind") == kind:
            return True
    return False


def _enqueue_job(queue_dir: Path, kind: str, extra: dict | None = None) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    job = {"kind": kind, "created": datetime.now(timezone.utc).isoformat()}
    if extra:
        job.update(extra)
    job_path = queue_dir / f"{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}.json"
    tmp_path = job_path.with_suffix(job_path.suffix + ".tmp")
    try:
        fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(job))
        os.replace(tmp_path, job_path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _start_worker(kit_home: Path) -> None:
    worker = Path(__file__).resolve().parent / "_auto_update_worker.py"
    try:
        subprocess.Popen(
            [sys.executable, str(worker)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def check(kit_home: Path | None = None) -> None:
    """Entry point for wiki_loader.run(). Callers must wrap this in a
    try/except so a bug here never breaks the SessionStart context output;
    it also independently checks is_nested() so it is safe to call
    directly (not just via wiki_loader's own main_guard)."""
    if _common.is_nested():
        return

    kit_home = kit_home if kit_home is not None else _common.kit_home()
    config = _common.load_config(kit_home)
    if not config:
        return
    watch_repos = config.get("watch_repos") or []
    if not watch_repos:
        return

    state_dir = kit_home / "state"
    state_path = state_dir / "maintenance.json"
    existed = state_path.exists()
    if not existed:
        _create_state_if_missing(state_path)

    state = _read_state(state_path)
    if state is None:
        if existed:
            _common.log(kit_home, "maintenance.json unreadable")
        return

    if not _due(_last_merge_check_dt(state), CHECK_INTERVAL_SECONDS):
        return

    queue_dir = state_dir / "auto-update"
    if not _has_pending_job(queue_dir, "refresh"):
        _enqueue_job(queue_dir, "refresh")
    # A job of this kind already queued does not skip starting the worker —
    # the worker's flock makes an extra start harmless, and skipping it
    # here would leave that queued job stuck if the worker that was
    # supposed to drain it already exited.
    _start_worker(kit_home)


def check_health(kit_home: Path | None = None, cli: str = "") -> bool:
    """The 30-day health-check rule, called from wiki_auto_update.run()
    before any of its early returns. Creates `maintenance.json` with a
    fresh `last_health` baseline when it does not exist yet (a brand-new
    install has nothing to health-check), and otherwise enqueues a
    `{"kind": "health", "cli": ...}` job — deduped against one already
    queued — once 30 days have passed since the last one. Starts the
    worker itself and returns True when it queued a *new* job, so the
    caller knows a background run is now pending even if its own
    session-job path finds nothing to do."""
    if _common.is_nested():
        return False

    kit_home = kit_home if kit_home is not None else _common.kit_home()
    state_dir = kit_home / "state"
    state_path = state_dir / "maintenance.json"
    existed = state_path.exists()
    if not existed:
        _create_state_if_missing(state_path)
        return False

    state = _read_state(state_path)
    if state is None:
        _common.log(kit_home, "maintenance.json unreadable")
        return False

    if not _due(_last_health_dt(state), HEALTH_INTERVAL_SECONDS):
        return False

    queue_dir = state_dir / "auto-update"
    if _has_pending_job(queue_dir, "health"):
        _start_worker(kit_home)  # see check(): an extra start is harmless
        return False

    _enqueue_job(queue_dir, "health", {"cli": cli})
    _start_worker(kit_home)
    return True
