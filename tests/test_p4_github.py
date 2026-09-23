from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import kit.github as github_mod
from kit.github import GitHub, fetch_repo, gh_cli_token


def _body(obj):
    return json.dumps(obj).encode("utf-8")


class FakeOpener:
    """Records calls and serves paginated /user/repos-shaped responses."""

    def __init__(self, pages, single=None):
        self.pages = pages
        self.single = single or {}
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if url in self.single:
            return 200, {}, _body(self.single[url])
        for page_url, items, next_url in self.pages:
            if url == page_url:
                headers_out = {}
                if next_url:
                    headers_out["Link"] = f'<{next_url}>; rel="next"'
                return 200, headers_out, _body(items)
        raise AssertionError(f"unexpected url {url}")


class GitHubClientTest(unittest.TestCase):
    def test_list_repos_follows_pagination_and_sends_bearer(self):
        page1_url = (
            "https://api.github.com/user/repos"
            "?per_page=100&sort=pushed&affiliation=owner,collaborator,organization_member"
        )
        page2_url = page1_url + "&page=2"
        repo_a = {"full_name": "acme/a", "default_branch": "main", "private": False, "pushed_at": "2024-01-01"}
        repo_b = {"full_name": "acme/b", "default_branch": "master", "private": True, "pushed_at": "2024-02-01"}
        opener = FakeOpener(
            pages=[
                (page1_url, [repo_a], page2_url),
                (page2_url, [repo_b], None),
            ]
        )
        gh = GitHub("tok123", opener=opener)
        repos = gh.list_repos()

        self.assertEqual([r["full_name"] for r in repos], ["acme/a", "acme/b"])
        self.assertEqual(repos[0]["default_branch"], "main")
        self.assertEqual(repos[1]["private"], True)

        for _url, headers in opener.calls:
            self.assertEqual(headers["Authorization"], "Bearer tok123")
            self.assertEqual(headers["Accept"], "application/vnd.github+json")
            self.assertEqual(headers["User-Agent"], "keel")

    def test_list_branches_returns_names(self):
        url = "https://api.github.com/repos/acme/a/branches?per_page=100"
        opener = FakeOpener(pages=[(url, [{"name": "main"}, {"name": "dev"}], None)])
        gh = GitHub("tok", opener=opener)
        self.assertEqual(gh.list_branches("acme/a"), ["main", "dev"])

    def test_whoami_returns_login(self):
        opener = FakeOpener(pages=[], single={"https://api.github.com/user": {"login": "octocat"}})
        gh = GitHub("tok", opener=opener)
        self.assertEqual(gh.whoami(), "octocat")

    def test_get_repo_returns_decoded_object(self):
        repo = {"full_name": "acme/a", "default_branch": "trunk", "private": False}
        opener = FakeOpener(pages=[], single={"https://api.github.com/repos/acme/a": repo})
        gh = GitHub("tok", opener=opener)
        self.assertEqual(gh.get_repo("acme/a"), repo)


class ServerDefaultBranchFallbackTest(unittest.TestCase):
    """Task 5.8: the server's default-branch fallback must go through the
    public `GitHub.get_repo`, not reach into the private `_get_all_pages`."""

    def test_default_branch_fallback_calls_get_repo(self):
        server_path = Path(__file__).resolve().parent.parent / "app" / "server.py"
        src = server_path.read_text(encoding="utf-8")
        start = src.index("def _github_default_branch")
        end = src.index("\ndef ", start + 1)
        block = src[start:end]
        self.assertIn("gh.get_repo(", block)
        self.assertNotIn("_get_all_pages", block)


class GhCliTokenTest(unittest.TestCase):
    def test_returns_none_on_failure(self):
        with mock.patch.object(
            github_mod.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not logged in"),
        ):
            self.assertIsNone(gh_cli_token())

    def test_returns_stripped_token_on_success(self):
        with mock.patch.object(
            github_mod.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="ghp_abc123\n", stderr=""),
        ):
            self.assertEqual(gh_cli_token(), "ghp_abc123")


class FetchRepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache_dir = Path(self.tmp) / "cache"

    def test_failure_message_redacts_token(self):
        token = "ghp_supersecrettoken"
        token_b64 = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        recorded = {}

        def fake_run(args, capture_output, text, env, check):
            recorded["args"] = args
            recorded["env"] = env
            return subprocess.CompletedProcess(
                args=args,
                returncode=128,
                stdout="",
                stderr=f"fatal: could not auth with header AUTHORIZATION: basic {token_b64} raw={token}",
            )

        with mock.patch.object(github_mod.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_repo("acme/widget", "main", self.cache_dir, token)

        message = str(ctx.exception)
        self.assertNotIn(token, message)
        self.assertNotIn(token_b64, message)

        for arg in recorded["args"]:
            self.assertNotIn(token, arg)
            self.assertNotIn(token_b64, arg)

    def test_success_never_writes_token_to_dest_config(self):
        token = "ghp_supersecrettoken"

        def fake_run(args, capture_output, text, env, check):
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "config").write_text("[core]\n\tbare = true\n", encoding="utf-8")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch.object(github_mod.subprocess, "run", side_effect=fake_run):
            dest = fetch_repo("acme/widget", "main", self.cache_dir, token)

        config_text = (dest / "config").read_text(encoding="utf-8")
        self.assertNotIn(token, config_text)
        self.assertEqual(dest, self.cache_dir / "acme" / "widget.git")

    def test_clone_uses_full_blob_none_clone_not_shallow(self):
        token = "ghp_tok"
        recorded = {}

        def fake_run(args, capture_output, text, env, check):
            recorded["args"] = args
            recorded["env"] = env
            dest = Path(args[-1])
            dest.mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch.object(github_mod.subprocess, "run", side_effect=fake_run):
            fetch_repo("acme/widget", "main", self.cache_dir, token)

        args = recorded["args"]
        self.assertIn("--filter=blob:none", args)
        self.assertFalse(any(a.startswith("--shallow-since") for a in args))
        self.assertEqual(recorded["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_fetch_refspec_and_no_shallow_since_when_dest_exists(self):
        token = "ghp_tok"
        dest = self.cache_dir / "acme" / "widget.git"
        dest.mkdir(parents=True, exist_ok=True)
        recorded = {}

        def fake_run(args, capture_output, text, env, check):
            recorded["args"] = args
            recorded["env"] = env
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch.object(github_mod.subprocess, "run", side_effect=fake_run):
            fetch_repo("acme/widget", "main", self.cache_dir, token)

        args = recorded["args"]
        self.assertIn("--filter=blob:none", args)
        self.assertFalse(any(a.startswith("--shallow-since") for a in args))
        self.assertIn("+main:main", args)
        self.assertEqual(recorded["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_unshallows_existing_shallow_cache_before_fetching(self):
        token = "ghp_tok"
        dest = self.cache_dir / "acme" / "widget.git"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "shallow").write_text("deadbeef\n", encoding="utf-8")
        recorded = {}

        def fake_run(args, capture_output, text, env, check):
            recorded["args"] = args
            recorded["env"] = env
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with mock.patch.object(github_mod.subprocess, "run", side_effect=fake_run):
            fetch_repo("acme/widget", "main", self.cache_dir, token)

        args = recorded["args"]
        self.assertIn("--unshallow", args)
        self.assertIn("+main:main", args)

    def test_fetch_repo_signature_has_no_since_days(self):
        import inspect

        params = list(inspect.signature(fetch_repo).parameters)
        self.assertNotIn("since_days", params)


class DefaultOpenerTimeoutTest(unittest.TestCase):
    def test_default_opener_passes_timeout(self):
        recorded = {}

        class FakeResp:
            status = 200
            headers = {}

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            recorded["timeout"] = timeout
            return FakeResp()

        with mock.patch.object(github_mod.urllib.request, "urlopen", side_effect=fake_urlopen):
            github_mod._default_opener("https://api.github.com/user", {})

        self.assertEqual(recorded["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
