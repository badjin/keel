"""Task 6.24: per-repo GitHub merge-watch — config recording (server) and
the worker's refresh-on-new-head decision.
Task 6.25: the 30-day automatic health-check rule in `_maintenance`."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app import server as server_mod

ROOT = Path(__file__).resolve().parents[1]
HOOKS_SRC = ROOT / "kit" / "hooks"


def load_worker():
    spec = importlib.util.spec_from_file_location(
        "_auto_update_worker_merge_watch", HOOKS_SRC / "_auto_update_worker.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_maintenance():
    spec = importlib.util.spec_from_file_location(
        "_maintenance_merge_watch", HOOKS_SRC / "_maintenance.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_bare_cache_repo(cache_dir: Path, repo: str, branch: str) -> str:
    """Builds a tiny real repo, commits once on `branch`, and bare-clones it
    to <cache_dir>/<owner>/<name>.git — the shape _cached_local_head reads
    with real git plumbing (no fake needed for this half of the test).
    Returns the branch's head sha."""
    owner, name = repo.split("/", 1)
    src = cache_dir / "_src"
    src.mkdir(parents=True)
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@test.local",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@test.local",
    })
    subprocess.run(["git", "-c", "commit.gpgsign=false", "init", "-q", "-b", branch], cwd=str(src), env=env, check=True, capture_output=True)
    (src / "f.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(src), env=env, check=True, capture_output=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], cwd=str(src), env=env, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", branch], cwd=str(src), env=env, check=True, capture_output=True, text=True,
    ).stdout.strip()

    dest = cache_dir / owner / f"{name}.git"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--bare", "-q", str(src), str(dest)], check=True, capture_output=True)
    return sha


class WatchListMergeWriteTest(unittest.TestCase):
    """Step 1.1: recording a GitHub watch entry merges into config.json
    without disturbing the other keys, replaces a repeat entry instead of
    duplicating it, and removing it (watch=False) drops it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config_path = self.home / ".keel" / "config.json"
        self.config_path.parent.mkdir(parents=True)
        self.base_config = {
            "wiki_path": str(self.home / "wiki"),
            "ui_language": "ko",
            "handoff_threshold": 60,
            "handoff_targets": ["claude"],
        }
        self.config_path.write_text(json.dumps(self.base_config), encoding="utf-8")

    def _read_config(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def test_watch_true_adds_entry_and_keeps_other_keys(self):
        server_mod._record_watch_repo(self.home, "acme/widget", "main", True, 90, "ko", None)
        data = self._read_config()
        for key, value in self.base_config.items():
            self.assertEqual(data[key], value)
        self.assertEqual(
            data["watch_repos"],
            [{"repo": "acme/widget", "branch": "main", "days": 90, "language": "ko", "llm": None}],
        )

    def test_recording_same_repo_again_replaces_not_duplicates(self):
        server_mod._record_watch_repo(self.home, "acme/widget", "main", True, 90, "ko", None)
        server_mod._record_watch_repo(self.home, "acme/widget", "main", True, 120, "en", "claude")
        data = self._read_config()
        self.assertEqual(
            data["watch_repos"],
            [{"repo": "acme/widget", "branch": "main", "days": 120, "language": "en", "llm": "claude"}],
        )

    def test_watch_false_removes_entry(self):
        server_mod._record_watch_repo(self.home, "acme/widget", "main", True, 90, "ko", None)
        server_mod._record_watch_repo(self.home, "acme/widget", "main", False, 90, "ko", None)
        data = self._read_config()
        self.assertEqual(data["watch_repos"], [])

    def test_recording_same_repo_on_a_different_branch_leaves_one_entry(self):
        # Task 6.26 item 1: pages are rendered per repo, not per branch, so
        # a repo can never have more than one watch_repos entry regardless
        # of which branch it is recorded on.
        server_mod._record_watch_repo(self.home, "badjin/x", "main", True, 90, "ko", None)
        server_mod._record_watch_repo(self.home, "badjin/x", "dev", True, 90, "ko", None)
        data = self._read_config()
        self.assertEqual(
            data["watch_repos"],
            [{"repo": "badjin/x", "branch": "dev", "days": 90, "language": "ko", "llm": None}],
        )

    def test_watch_false_removes_every_entry_for_repo_regardless_of_branch(self):
        server_mod._record_watch_repo(self.home, "badjin/x", "main", True, 90, "ko", None)
        server_mod._record_watch_repo(self.home, "badjin/x", "dev", True, 90, "ko", None)
        server_mod._record_watch_repo(self.home, "badjin/x", "other", False, 90, "ko", None)
        data = self._read_config()
        self.assertEqual(data["watch_repos"], [])
        for key, value in self.base_config.items():
            self.assertEqual(data[key], value)


class RefreshOnNewHeadTest(unittest.TestCase):
    """Step 1.2: process_refresh only re-renders a repo whose remote head
    moved, and a first-seen repo takes its baseline from a cached bare
    clone (no render) or gets a full render when no cache exists."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit_home = Path(self.tmp.name) / "kit_home"
        self.kit_home.mkdir()
        self.wiki_path = Path(self.tmp.name) / "wiki"
        self.wiki_path.mkdir()
        self.worker = load_worker()

    def _write_config(self, watch_repos):
        (self.kit_home / "config.json").write_text(
            json.dumps({"wiki_path": str(self.wiki_path), "watch_repos": watch_repos}),
            encoding="utf-8",
        )

    def _state(self) -> dict:
        return json.loads((self.kit_home / "state" / "maintenance.json").read_text(encoding="utf-8"))

    def test_unchanged_head_is_not_rerendered(self):
        self._write_config([{"repo": "acme/unchanged", "branch": "main", "days": 90, "language": "ko", "llm": None}])
        self.worker._save_maintenance_state(
            self.kit_home, {"last_merge_check": "1970-01-01T00:00:00+00:00", "shas": {"acme/unchanged@main": "sha-old"}}
        )
        render_calls = []
        with mock.patch.object(self.worker, "_remote_head", return_value="sha-old"), \
             mock.patch.object(self.worker, "_fetch_and_render", side_effect=lambda *a, **k: render_calls.append(a) or "should-not-happen"):
            self.worker.process_refresh(self.kit_home)

        self.assertEqual(render_calls, [])
        self.assertEqual(self._state()["shas"]["acme/unchanged@main"], "sha-old")

    def test_different_head_is_fetched_and_rendered_and_recorded(self):
        self._write_config([{"repo": "acme/changed", "branch": "main", "days": 90, "language": "ko", "llm": None}])
        self.worker._save_maintenance_state(
            self.kit_home, {"last_merge_check": "1970-01-01T00:00:00+00:00", "shas": {"acme/changed@main": "sha-old"}}
        )
        render_calls = []
        with mock.patch.object(self.worker, "_remote_head", return_value="sha-new"), \
             mock.patch.object(self.worker, "_fetch_and_render", side_effect=lambda *a, **k: render_calls.append(a) or "sha-rendered"):
            self.worker.process_refresh(self.kit_home)

        self.assertEqual(len(render_calls), 1)
        self.assertEqual(self._state()["shas"]["acme/changed@main"], "sha-rendered")

    def test_first_seen_repo_with_cache_takes_baseline_without_rendering(self):
        cache_dir = self.kit_home / "cache"
        cache_dir.mkdir(parents=True)
        sha = _make_bare_cache_repo(cache_dir, "acme/cached", "main")
        self._write_config([{"repo": "acme/cached", "branch": "main", "days": 90, "language": "ko", "llm": None}])

        with mock.patch.object(self.worker, "_fetch_and_render", side_effect=AssertionError("must not render")):
            self.worker.process_refresh(self.kit_home)

        self.assertEqual(self._state()["shas"]["acme/cached@main"], sha)

    def test_first_seen_repo_without_cache_is_rendered(self):
        self._write_config([{"repo": "acme/new", "branch": "main", "days": 90, "language": "ko", "llm": None}])
        render_calls = []
        with mock.patch.object(self.worker, "_fetch_and_render", side_effect=lambda *a, **k: render_calls.append(a) or "sha-first"):
            self.worker.process_refresh(self.kit_home)

        self.assertEqual(len(render_calls), 1)
        self.assertEqual(self._state()["shas"]["acme/new@main"], "sha-first")

    def test_last_merge_check_is_updated_after_a_run(self):
        self._write_config([{"repo": "acme/new", "branch": "main", "days": 90, "language": "ko", "llm": None}])
        with mock.patch.object(self.worker, "_fetch_and_render", return_value="sha-first"):
            self.worker.process_refresh(self.kit_home)
        self.assertNotEqual(self._state()["last_merge_check"], "1970-01-01T00:00:00+00:00")


class HealthCheckThirtyDayRuleTest(unittest.TestCase):
    """Step 1 (Task 6.25): the 30-day rule in `_maintenance.check_health`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit_home = Path(self.tmp.name) / "kit_home"
        self.kit_home.mkdir()
        self.maintenance = load_maintenance()

    def _state_path(self) -> Path:
        return self.kit_home / "state" / "maintenance.json"

    def _job_files(self):
        d = self.kit_home / "state" / "auto-update"
        if not d.exists():
            return []
        return list(d.glob("*.json"))

    def _write_state(self, last_health_dt) -> None:
        state_dir = self.kit_home / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(
            json.dumps({
                "last_merge_check": "1970-01-01T00:00:00+00:00",
                "last_health": last_health_dt.isoformat(),
                "shas": {},
            }),
            encoding="utf-8",
        )

    def test_missing_state_is_created_with_last_health_now_and_no_job(self):
        with mock.patch.object(self.maintenance, "_start_worker") as start_worker:
            result = self.maintenance.check_health(self.kit_home, cli="claude")

        self.assertFalse(result)
        start_worker.assert_not_called()
        self.assertEqual(self._job_files(), [])
        state = json.loads(self._state_path().read_text(encoding="utf-8"))
        last_health = datetime.fromisoformat(state["last_health"])
        self.assertLess((datetime.now(timezone.utc) - last_health).total_seconds(), 5)

    def test_31_days_ago_queues_exactly_one_health_job(self):
        self._write_state(datetime.now(timezone.utc) - timedelta(days=31))
        with mock.patch.object(self.maintenance, "_start_worker") as start_worker:
            result = self.maintenance.check_health(self.kit_home, cli="claude")

        self.assertTrue(result)
        start_worker.assert_called_once()
        jobs = self._job_files()
        self.assertEqual(len(jobs), 1)
        job = json.loads(jobs[0].read_text(encoding="utf-8"))
        self.assertEqual(job["kind"], "health")
        self.assertEqual(job["cli"], "claude")

    def test_second_call_while_job_still_queued_stays_at_one(self):
        self._write_state(datetime.now(timezone.utc) - timedelta(days=31))
        with mock.patch.object(self.maintenance, "_start_worker"):
            self.maintenance.check_health(self.kit_home, cli="claude")
            second_result = self.maintenance.check_health(self.kit_home, cli="claude")

        self.assertFalse(second_result)
        self.assertEqual(len(self._job_files()), 1)

    def test_10_days_ago_queues_nothing(self):
        self._write_state(datetime.now(timezone.utc) - timedelta(days=10))
        with mock.patch.object(self.maintenance, "_start_worker") as start_worker:
            result = self.maintenance.check_health(self.kit_home, cli="claude")

        self.assertFalse(result)
        start_worker.assert_not_called()
        self.assertEqual(self._job_files(), [])


if __name__ == "__main__":
    unittest.main()
