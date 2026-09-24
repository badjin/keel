"""Task 5.1: server core — auth, static serving, /api/env, /api/catalogue, hook install/uninstall."""
from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import app.server as server_mod
from app.server import make_server


def _request(port, path, method="GET", token=None, host=None, body=None, lang=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {}
    if token is not None:
        headers["X-Kit-Token"] = token
    if host is not None:
        headers["Host"] = host
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


class ServerCoreTest(unittest.TestCase):
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

    def _host(self):
        return f"127.0.0.1:{self.port}"

    def test_api_requires_token(self):
        status, body = _request(self.port, "/api/env", host=self._host(), lang="ko")
        self.assertEqual(status, 403)
        self.assertIn("error", body)

    def test_api_rejects_foreign_host(self):
        status, body = _request(self.port, "/api/env", token=self.token, host="evil.com", lang="ko")
        self.assertEqual(status, 403)
        self.assertIn("error", body)

    def test_api_rejects_token_of_different_length(self):
        status, body = _request(self.port, "/api/env", token=self.token + "x", host=self._host(), lang="ko")
        self.assertEqual(status, 403)
        self.assertIn("error", body)

    def test_unknown_api_get_route_returns_404(self):
        status, body = _request(self.port, "/api/does-not-exist", token=self.token, host=self._host(), lang="ko")
        self.assertEqual(status, 404)

    def test_unknown_api_post_route_returns_404(self):
        status, body = _request(
            self.port, "/api/does-not-exist", method="POST", token=self.token, host=self._host(), body={}, lang="ko"
        )
        self.assertEqual(status, 404)

    def test_token_comparison_uses_compare_digest(self):
        import secrets as secrets_mod

        with mock.patch.object(server_mod.secrets, "compare_digest", wraps=secrets_mod.compare_digest) as spy:
            status, body = _request(self.port, "/api/env", token=self.token, host=self._host())
        self.assertEqual(status, 200)
        spy.assert_called_once_with(self.token.encode("utf-8"), self.token.encode("utf-8"))

    def test_env_ok_with_token(self):
        status, body = _request(self.port, "/api/env", token=self.token, host=self._host())
        self.assertEqual(status, 200)
        self.assertEqual(body["home"], str(self.home))
        self.assertIn("clis", body)
        self.assertIn("claude", body["clis"])
        self.assertIn("codex", body["clis"])
        self.assertIn("gh_cli", body)
        self.assertIn("obsidian_hint", body)
        self.assertIsNone(body["config"])
        self.assertIn("version", body)

    def test_catalogue_lists_all_hooks(self):
        status, body = _request(self.port, "/api/catalogue", token=self.token, host=self._host())
        self.assertEqual(status, 200)
        self.assertEqual(len(body["catalogue"]), 12)
        self.assertIn("claude", body["installed"])
        self.assertIn("codex", body["installed"])

    def test_install_and_uninstall_round_trip_writes_only_into_home(self):
        status, body = _request(
            self.port,
            "/api/hooks/install",
            method="POST",
            token=self.token,
            host=self._host(),
            body={"ids": ["wiki-loader", "wiki-trigger"], "targets": ["claude"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(sorted(body["claude"]), ["wiki-loader", "wiki-trigger"])

        settings_path = self.home / ".claude" / "settings.json"
        self.assertTrue(settings_path.exists())
        hooks_dir = self.home / ".keel" / "hooks"
        self.assertTrue((hooks_dir / "wiki_loader.py").exists())

        status, body = _request(
            self.port,
            "/api/hooks/uninstall",
            method="POST",
            token=self.token,
            host=self._host(),
            body={"targets": ["claude"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["claude"], 2)

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        remaining = [
            h
            for groups in settings.get("hooks", {}).values()
            for grp in groups
            for h in grp.get("hooks", [])
        ]
        self.assertEqual(remaining, [])

    def test_hooks_install_alone_does_not_make_wiki_ready_endpoints_think_wiki_exists(self):
        # Recording ui_language at hook-install time writes a config.json
        # with no wiki_path. Every endpoint that gates on
        # `if not state.config(): raise wiki_not_ready` must still treat
        # that as "no wiki yet" — not KeyError on config["wiki_path"].
        status, body = _request(
            self.port,
            "/api/hooks/install",
            method="POST",
            token=self.token,
            host=self._host(),
            body={"ids": ["wiki-loader"], "targets": ["claude"]},
            lang="ko",
        )
        self.assertEqual(status, 200)

        status, body = _request(
            self.port, "/api/finish", method="POST", token=self.token, host=self._host(), body={}, lang="ko",
        )
        self.assertEqual(status, 400)

    def test_hooks_install_records_ui_language_even_before_wiki_created(self):
        status, body = _request(
            self.port,
            "/api/hooks/install",
            method="POST",
            token=self.token,
            host=self._host(),
            body={"ids": ["wiki-loader"], "targets": ["claude"]},
            lang="ko",
        )
        self.assertEqual(status, 200)
        config_path = self.home / ".keel" / "config.json"
        self.assertTrue(config_path.exists())
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config.get("ui_language"), "ko")
        self.assertNotIn("wiki_path", config)

    def test_uninstall_passes_remove_skills_through(self):
        from kit.skills_install import install_skills

        install_skills(self.home, self.home / "llm-wiki", ["claude"])
        skill_path = self.home / ".claude" / "skills" / "kb" / "SKILL.md"
        self.assertTrue(skill_path.exists())

        status, body = _request(
            self.port,
            "/api/hooks/uninstall",
            method="POST",
            token=self.token,
            host=self._host(),
            body={"targets": ["claude"], "remove_skills": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["skills_removed"]), 4)
        self.assertFalse(skill_path.exists())

    def test_static_serving_content_types_and_404s(self):
        fixtures = Path(self.tmp) / "static-fixtures"
        fixtures.mkdir()
        (fixtures / "index.html").write_text("<html></html>", encoding="utf-8")
        (fixtures / "x.css").write_text("body{}", encoding="utf-8")
        (fixtures / "x.js").write_text("1;", encoding="utf-8")
        (fixtures / "x.json").write_text("{}", encoding="utf-8")
        (fixtures / "x.svg").write_text("<svg></svg>", encoding="utf-8")

        cases = [
            ("/", "index.html", "text/html"),
            ("/static/x.css", "x.css", "text/css"),
            ("/static/x.js", "x.js", "application/javascript"),
            ("/static/x.json", "x.json", "application/json"),
            ("/static/x.svg", "x.svg", "image/svg+xml"),
        ]
        with mock.patch.object(server_mod, "STATIC_DIR", fixtures):
            for req_path, _fname, expected_type in cases:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
                conn.request("GET", req_path, headers={"Host": self._host()})
                resp = conn.getresponse()
                resp.read()
                conn.close()
                self.assertEqual(resp.status, 200, req_path)
                self.assertIn(expected_type, resp.getheader("Content-Type"), req_path)
                self.assertEqual(resp.getheader("Cache-Control"), "no-store", req_path)

            for missing_path in ("/static/nope.txt", "/static/../server.py"):
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
                conn.request("GET", missing_path, headers={"Host": self._host()})
                resp = conn.getresponse()
                resp.read()
                conn.close()
                self.assertEqual(resp.status, 404, missing_path)
                self.assertEqual(resp.getheader("Cache-Control"), "no-store", missing_path)


class ServerStartupResultRenameTest(unittest.TestCase):
    """Task 6.2 step 4: an existing install-result.json from a previous run
    is moved aside at server start so step 4's poll never sees a stale
    result from a run that already finished."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = Path(self.tmp) / "home"
        self.home.mkdir()

    def test_existing_result_file_is_renamed_to_prev(self):
        kit_dir = self.home / ".keel"
        kit_dir.mkdir()
        result_path = kit_dir / "install-result.json"
        result_path.write_text('{"wiki_path": "old"}', encoding="utf-8")

        server, _token = make_server(self.home, port=0)
        self.addCleanup(server.server_close)

        self.assertFalse(result_path.exists())
        prev_path = kit_dir / "install-result.prev.json"
        self.assertTrue(prev_path.exists())
        self.assertEqual(prev_path.read_text(encoding="utf-8"), '{"wiki_path": "old"}')

    def test_no_existing_result_file_is_a_no_op(self):
        server, _token = make_server(self.home, port=0)
        self.addCleanup(server.server_close)
        self.assertFalse((self.home / ".keel" / "install-result.json").exists())
        self.assertFalse((self.home / ".keel" / "install-result.prev.json").exists())


class ErrorTableBilingualTest(unittest.TestCase):
    """Task 6.15 step 1: server error messages come from a code-keyed
    en/ko table; default is English, ko only when X-Kit-Lang: ko is sent,
    and any other/missing header value falls back to English."""

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

    def _host(self):
        return f"127.0.0.1:{self.port}"

    def test_unauthorized_returns_403(self):
        status, body = _request(self.port, "/api/env", host=self._host())
        self.assertEqual(status, 403)
        self.assertIn("error", body)


class Task617ErrorsAndImportsTest(unittest.TestCase):
    """Task 6.17 step 1: kit errors pass through state.redact before being
    returned on both the API and job paths; the llm check uses code
    `llm_invalid`; kit.errors is imported once in app/server.py;
    progress()/message() share one implementation."""

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

    def _host(self):
        return f"127.0.0.1:{self.port}"

    def _wait(self, job_id, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.server.state.jobs.get(job_id)
            if job["state"] != "running":
                return job
            time.sleep(0.01)
        self.fail("job did not finish in time")

    def test_kit_value_error_message_passes_through_state_redact(self):
        spy = mock.Mock(wraps=self.server.state.redact)
        with mock.patch.object(self.server.state, "redact", spy):
            status, body = _request(
                self.port, "/api/wiki/init", method="POST", token=self.token, host=self._host(), body={},
            )
        self.assertEqual(status, 400)
        spy.assert_called_once()

    def test_generic_exception_message_also_passes_through_state_redact(self):
        import app.server as server_mod

        spy = mock.Mock(wraps=self.server.state.redact)
        with mock.patch.object(self.server.state, "redact", spy), mock.patch.object(
            server_mod.install_hooks, "load_catalogue", side_effect=RuntimeError("boom"),
        ):
            status, body = _request(
                self.port, "/api/catalogue", token=self.token, host=self._host(),
            )
        self.assertEqual(status, 400)
        spy.assert_called_once()

    def test_llm_check_uses_llm_invalid_code(self):
        from kit.errors import message

        wiki = self.home / "llm-wiki"
        status, body = _request(
            self.port, "/api/wiki/init", method="POST", token=self.token, host=self._host(),
            body={"path": str(wiki), "language": "en", "targets": []},
        )
        self.assertEqual(status, 200)

        status, body = _request(
            self.port, "/api/history/run", method="POST", token=self.token, host=self._host(),
            body={"items": [], "days": 90, "llm": "not-a-real-llm"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], message("llm_invalid", "en"))

    def test_job_path_error_message_passes_through_state_redact(self):
        wiki = self.home / "llm-wiki"
        status, _ = _request(
            self.port, "/api/wiki/init", method="POST", token=self.token, host=self._host(),
            body={"path": str(wiki), "language": "en", "targets": []},
        )
        self.assertEqual(status, 200)

        secret = "secrettoken123456"
        self.server.state.github_token = secret

        import app.server as server_mod
        from kit.errors import KitValueError

        with mock.patch.object(
            server_mod, "_run_history_item",
            side_effect=KitValueError("path_required"),
        ), mock.patch.object(
            server_mod, "err_message",
            return_value=f"leaked {secret} here",
        ):
            status, body = _request(
                self.port, "/api/history/run", method="POST", token=self.token, host=self._host(),
                body={"items": [{"kind": "local", "repo_or_path": "x"}], "days": 90},
            )
            self.assertEqual(status, 200)
            # The job runs in a background thread — wait for it while the
            # patches are still active, or the real (unpatched) functions
            # may run instead once this `with` block exits.
            job = self._wait(body["job"])
        self.assertNotIn(secret, "".join(job["log"]))

    def test_kit_errors_imported_once_in_server_module(self):
        import app.server as server_mod

        source = Path(server_mod.__file__).read_text(encoding="utf-8")
        import_lines = [
            line for line in source.splitlines()
            if ("import" in line) and ("kit.errors" in line or "kit import errors" in line)
        ]
        self.assertEqual(len(import_lines), 1, import_lines)

    def test_progress_and_message_share_one_lookup_implementation(self):
        import inspect
        import kit.errors as errors_mod

        progress_src = inspect.getsource(errors_mod.progress)
        message_src = inspect.getsource(errors_mod.message)
        # Neither wrapper should re-implement the lang-fallback/format-catch
        # logic itself; both should delegate to one shared helper.
        self.assertNotIn("try:", progress_src)
        self.assertNotIn("try:", message_src)


if __name__ == "__main__":
    unittest.main()
