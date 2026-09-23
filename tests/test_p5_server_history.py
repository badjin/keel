"""Task 5.3: history jobs and GitHub endpoints."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from app.server import make_server


def _request(port, path, method="GET", token=None, host=None, body=None, lang="ko"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"X-Kit-Token": token, "Host": host}
    if lang is not None:
        headers["X-Kit-Lang"] = lang
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    parsed = json.loads(raw.decode("utf-8")) if raw else None
    return resp.status, parsed


def _run(cmd, cwd, env):
    subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, check=True)


def _build_fixture_repo(repo: Path):
    repo.mkdir(parents=True)
    base_env = {"PATH": os.environ.get("PATH", "")}
    _run(["git", "-c", "commit.gpgsign=false", "init", "-q", "-b", "main"], repo, base_env)
    env = dict(base_env)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@test.local",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@test.local",
        }
    )
    now = datetime.now()
    date_str = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    env["GIT_AUTHOR_DATE"] = date_str
    env["GIT_COMMITTER_DATE"] = date_str
    (repo / "README.md").write_text("hello", encoding="utf-8")
    _run(["git", "add", "-A"], repo, env)
    _run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "Initial commit"], repo, env)


class ServerHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = Path(self.tmp) / "home"
        self.home.mkdir()
        self.server, self.token = make_server(self.home, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.host = f"127.0.0.1:{self.port}"

    def _req(self, path, method="GET", body=None, lang="ko"):
        return _request(self.port, path, method=method, token=self.token, host=self.host, body=body, lang=lang)

    def _wait_job(self, job_id, timeout=10, lang="ko"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, body = self._req(f"/api/jobs/{job_id}", lang=lang)
            self.assertEqual(status, 200)
            if body["state"] != "running":
                return body
            time.sleep(0.1)
        self.fail("job did not finish in time")

    def test_local_history_run_reaches_done_and_writes_index(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        repo_dir = self.home / "repos-src" / "fixture-repo"
        _build_fixture_repo(repo_dir)

        status, body = self._req(
            "/api/history/run",
            method="POST",
            body={
                "items": [{"kind": "local", "repo_or_path": str(repo_dir), "branch": "main"}],
                "days": 5,
                "language": "ko",
                "llm": None,
            },
        )
        self.assertEqual(status, 200)
        job = self._wait_job(body["job"])
        self.assertEqual(job["state"], "done")

        index_path = wiki_path / "repos" / "fixture-repo" / "index.md"
        self.assertTrue(index_path.exists())

    def test_local_branches_returns_list_and_default(self):
        repo_dir = self.home / "repos-src" / "branch-repo"
        _build_fixture_repo(repo_dir)
        status, body = self._req(f"/api/local/branches?path={repo_dir}")
        self.assertEqual(status, 200)
        self.assertIn("main", body["branches"])
        self.assertEqual(body["default"], "main")

    def test_local_branches_outside_home_returns_400(self):
        status, body = self._req("/api/local/branches?path=/etc")
        self.assertEqual(status, 400)

    def test_local_branches_non_git_folder_returns_400(self):
        plain_dir = self.home / "not-a-repo"
        plain_dir.mkdir()
        status, body = self._req(f"/api/local/branches?path={plain_dir}")
        self.assertEqual(status, 400)

    def test_local_history_run_with_null_branch_uses_default(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        repo_dir = self.home / "repos-src" / "null-branch-repo"
        _build_fixture_repo(repo_dir)

        status, body = self._req(
            "/api/history/run",
            method="POST",
            body={
                "items": [{"kind": "local", "repo_or_path": str(repo_dir), "branch": None}],
                "days": 5,
                "language": "ko",
                "llm": None,
            },
        )
        self.assertEqual(status, 200)
        job = self._wait_job(body["job"])
        self.assertEqual(job["state"], "done")

        index_path = wiki_path / "repos" / "null-branch-repo" / "index.md"
        self.assertTrue(index_path.exists())

    def test_job_status_unknown_id_returns_404(self):
        status, body = self._req("/api/jobs/does-not-exist")
        self.assertEqual(status, 404)

    def test_days_five_clamps_to_thirty(self):
        from app.server import _clamp_days

        self.assertEqual(_clamp_days(5), 30)
        self.assertEqual(_clamp_days(400), 180)
        self.assertEqual(_clamp_days(90), 90)

    def test_github_token_returns_login_and_token_not_leaked(self):
        with mock.patch("app.server.github.GitHub.whoami", return_value="octocat"):
            status, body = self._req("/api/github/token", method="POST", body={"token": "sekrit-token"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"login": "octocat"})

        status, env_body = self._req("/api/env")
        self.assertNotIn("sekrit-token", json.dumps(env_body))

    def test_github_branches_error_redacts_token_in_400_body(self):
        with mock.patch("app.server.github.GitHub.whoami", return_value="octocat"):
            self._req("/api/github/token", method="POST", body={"token": "sekrit-token"})

        with mock.patch(
            "app.server.github.GitHub.list_branches",
            side_effect=RuntimeError("boom sekrit-token"),
        ):
            status, body = self._req("/api/github/branches?repo=acme/widgets")
        self.assertEqual(status, 400)
        self.assertNotIn("sekrit-token", json.dumps(body))

    def test_history_run_error_redacts_token_in_log(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        with mock.patch("app.server.github.GitHub.whoami", return_value="octocat"):
            self._req("/api/github/token", method="POST", body={"token": "sekrit-token"})

        with mock.patch("app.server.github.fetch_repo", side_effect=RuntimeError("boom sekrit-token")):
            status, body = self._req(
                "/api/history/run",
                method="POST",
                body={
                    "items": [{"kind": "github", "repo_or_path": "acme/widgets", "branch": "main"}],
                    "days": 30,
                    "language": "ko",
                    "llm": None,
                },
            )
        job = self._wait_job(body["job"])
        # every item in the job failed, so the job itself ends in error (Task 5.7)
        self.assertEqual(job["state"], "error")
        log_text = "\n".join(job["log"])
        self.assertNotIn("sekrit-token", log_text)

    def test_github_token_strips_whitespace_and_passes_clean_token(self):
        captured = {}

        class FakeGitHub:
            def __init__(self, token):
                captured["token"] = token

            def whoami(self):
                return "octocat"

        with mock.patch("app.server.github.GitHub", FakeGitHub):
            status, body = self._req("/api/github/token", method="POST", body={"token": "  abc\n"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"login": "octocat"})
        self.assertEqual(captured["token"], "abc")

    def test_github_token_whoami_failure_redacts_candidate_token(self):
        with mock.patch(
            "app.server.github.GitHub.whoami",
            side_effect=RuntimeError("unauthorized: sekrit-token"),
        ):
            status, body = self._req("/api/github/token", method="POST", body={"token": "sekrit-token"})
        self.assertEqual(status, 400)
        self.assertNotIn("sekrit-token", body["error"])
        self.assertIn("***", body["error"])

    def test_history_run_before_wiki_init_returns_400_no_fallback(self):
        status, body = self._req(
            "/api/history/run",
            method="POST",
            body={"items": [], "days": 30, "language": "ko", "llm": None},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "위키가 아직 만들어지지 않았습니다")

    def test_finish_before_wiki_init_returns_400_no_fallback(self):
        status, body = self._req("/api/finish", method="POST", body={})
        self.assertEqual(status, 400)

    def test_history_run_partial_failure_still_done_with_failures_logged(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        good_repo = self.home / "repos-src" / "good-repo"
        _build_fixture_repo(good_repo)
        bad_dir = self.home / "repos-src" / "not-a-repo"
        bad_dir.mkdir(parents=True)

        status, body = self._req(
            "/api/history/run",
            method="POST",
            body={
                "items": [
                    {"kind": "local", "repo_or_path": str(good_repo), "branch": "main"},
                    {"kind": "local", "repo_or_path": str(bad_dir), "branch": "main"},
                ],
                "days": 5,
                "language": "ko",
                "llm": None,
            },
        )
        self.assertEqual(status, 200)
        job = self._wait_job(body["job"])
        self.assertEqual(job["state"], "done")
        self.assertTrue(any(str(bad_dir) in line for line in job["log"]))

    def test_github_item_without_branch_uses_cached_default_branch(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        with mock.patch("app.server.github.GitHub.whoami", return_value="octocat"):
            self._req("/api/github/token", method="POST", body={"token": "tok"})
        with mock.patch(
            "app.server.github.GitHub.list_repos",
            return_value=[
                {"full_name": "acme/widgets", "default_branch": "develop", "private": False, "pushed_at": None}
            ],
        ):
            self._req("/api/github/repos")

        captured = {}

        def fake_fetch_repo(full_name, branch, cache_dir, token):
            captured["branch"] = branch
            raise RuntimeError("stop-before-real-git")

        with mock.patch("app.server.github.fetch_repo", side_effect=fake_fetch_repo):
            status, body = self._req(
                "/api/history/run",
                method="POST",
                body={
                    "items": [{"kind": "github", "repo_or_path": "acme/widgets", "branch": None}],
                    "days": 30,
                    "language": "ko",
                    "llm": None,
                },
            )
        self._wait_job(body["job"])
        self.assertEqual(captured["branch"], "develop")

    def test_github_item_without_branch_falls_back_to_api_when_not_cached(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        with mock.patch("app.server.github.GitHub.whoami", return_value="octocat"):
            self._req("/api/github/token", method="POST", body={"token": "tok"})
        # no /api/github/repos call yet, so the cache is empty and the endpoint must ask the API.

        captured = {}

        def fake_fetch_repo(full_name, branch, cache_dir, token):
            captured["branch"] = branch
            raise RuntimeError("stop-before-real-git")

        with mock.patch(
            "app.server.github.GitHub._get_all_pages",
            return_value=[{"full_name": "acme/widgets", "default_branch": "trunk", "private": False}],
        ) as mock_get_all_pages, mock.patch("app.server.github.fetch_repo", side_effect=fake_fetch_repo):
            status, body = self._req(
                "/api/history/run",
                method="POST",
                body={
                    "items": [{"kind": "github", "repo_or_path": "acme/widgets", "branch": None}],
                    "days": 30,
                    "language": "ko",
                    "llm": None,
                },
            )
        self._wait_job(body["job"])
        self.assertEqual(captured["branch"], "trunk")
        mock_get_all_pages.assert_called_once_with("https://api.github.com/repos/acme/widgets")

    def test_second_history_run_while_one_is_running_returns_409(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        started = threading.Event()
        release = threading.Event()

        def slow_item(state, item, days, language, llm, log, ui_lang="ko"):
            started.set()
            release.wait(timeout=5)
            return []

        item_body = {
            "items": [{"kind": "local", "repo_or_path": str(self.home), "branch": "main"}],
            "days": 30,
            "language": "ko",
            "llm": None,
        }
        try:
            with mock.patch("app.server._run_history_item", side_effect=slow_item):
                status1, body1 = self._req("/api/history/run", method="POST", body=item_body)
                self.assertEqual(status1, 200)
                self.assertTrue(started.wait(timeout=5))
                status2, _ = self._req("/api/history/run", method="POST", body=item_body)
        finally:
            release.set()
        self._wait_job(body1["job"])
        self.assertEqual(status2, 409)


if __name__ == "__main__":
    unittest.main()
