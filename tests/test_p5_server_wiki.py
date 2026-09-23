"""Task 5.2: wiki endpoints — init, fs/list, local index, obsidian/open, finish."""
from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.server import make_server


def _request(port, path, method="GET", token=None, host=None, body=None, lang=None):
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


class ServerWikiTest(unittest.TestCase):
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

    def _req(self, path, method="GET", body=None, lang=None):
        return _request(self.port, path, method=method, token=self.token, host=self.host, body=body, lang=lang)

    def test_fs_list_refuses_outside_home(self):
        status, body = self._req("/api/fs/list?path=/etc", lang="ko")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_fs_list_default_is_home(self):
        (self.home / "child").mkdir()
        status, body = self._req("/api/fs/list")
        self.assertEqual(status, 200)
        self.assertEqual(body["path"], str(self.home.resolve()))
        self.assertIsNone(body["parent"])
        self.assertEqual([d["name"] for d in body["dirs"]], ["child"])

    def test_wiki_init_creates_skeleton_and_skills(self):
        wiki_path = self.home / "llm-wiki"
        status, body = self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": ["claude"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue((wiki_path / "index.md").exists())
        self.assertTrue((wiki_path / "schema.md").exists())
        self.assertGreater(len(body["created"]), 0)
        self.assertGreater(len(body["skills"]), 0)
        self.assertTrue((self.home / ".claude" / "skills" / "kb" / "SKILL.md").exists())

    def test_wiki_local_writes_local_index(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        folder = self.home / "myproject"
        (folder).mkdir()
        (folder / "README.md").write_text("hello", encoding="utf-8")

        status, body = self._req(
            "/api/wiki/local",
            method="POST",
            body={"folders": [str(folder)]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["written"]), 1)
        self.assertTrue((wiki_path / "local" / "myproject" / "index.md").exists())

    def test_obsidian_open_returns_url_with_subprocess_monkeypatched(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        with mock.patch("app.server.subprocess.run") as mock_run:
            status, body = self._req("/api/obsidian/open", method="POST", body={})
        self.assertEqual(status, 200)
        self.assertTrue(body["url"].startswith("obsidian://open?path="))
        if mock_run.called:
            self.assertEqual(mock_run.call_args[0][0][0], "open")

    def test_wiki_init_expands_tilde_against_server_home_not_process_home(self):
        status, body = self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": "~/my-wiki", "language": "ko", "targets": []},
        )
        self.assertEqual(status, 200)
        self.assertTrue((self.home / "my-wiki" / "index.md").exists())

    def test_wiki_init_outside_home_returns_400(self):
        outside = Path(self.tmp) / "outside-wiki"
        status, body = self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(outside), "language": "ko", "targets": []},
            lang="ko",
        )
        self.assertEqual(status, 400)
        self.assertFalse(outside.exists())

    def test_wiki_local_expands_tilde_folder_against_server_home(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        folder = self.home / "tilde-project"
        folder.mkdir()
        (folder / "README.md").write_text("hello", encoding="utf-8")

        status, body = self._req(
            "/api/wiki/local",
            method="POST",
            body={"folders": ["~/tilde-project"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["written"]), 1)
        self.assertTrue((wiki_path / "local" / "tilde-project" / "index.md").exists())

    def test_wiki_local_returns_git_repos_one_level_down(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        folder = self.home / "myproject"
        folder.mkdir()
        repo_a = folder / "repo-a"
        repo_a.mkdir()
        (repo_a / ".git").mkdir()
        repo_b = folder / "repo-b"
        repo_b.mkdir()
        (repo_b / ".git").mkdir()
        (folder / "plain").mkdir()

        status, body = self._req(
            "/api/wiki/local",
            method="POST",
            body={"folders": [str(folder)]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            sorted(body["git_repos"]), sorted([str(repo_a.resolve()), str(repo_b.resolve())])
        )

    def test_wiki_local_git_repos_includes_chosen_folder_itself(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        folder = self.home / "selfrepo"
        folder.mkdir()
        (folder / ".git").mkdir()

        status, body = self._req(
            "/api/wiki/local",
            method="POST",
            body={"folders": [str(folder)]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["git_repos"], [str(folder.resolve())])

    def test_finish_writes_install_result_json(self):
        wiki_path = self.home / "llm-wiki"
        self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
        )
        status, body = self._req("/api/finish", method="POST", body={})
        self.assertEqual(status, 200)
        self.assertEqual(body["wiki_path"], str(wiki_path))
        result_path = self.home / ".keel" / "install-result.json"
        self.assertTrue(result_path.exists())
        on_disk = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["wiki_path"], str(wiki_path))


class ServerWikiLocationPickerGuardTest(ServerWikiTest):
    """Task 6.13 step 1: even if the client-side folder-name picker were
    bypassed, a combined path that escapes home via `..` is still refused
    by the server (defense in depth; the picker builds the path client-side
    and sends it as a normal /api/wiki/init call)."""

    def test_wiki_init_rejects_combined_path_that_escapes_home_via_dotdot(self):
        escaping_path = str(self.home / "sub" / ".." / ".." / "outside-wiki")
        status, body = self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": escaping_path, "language": "ko", "targets": []},
            lang="ko",
        )
        self.assertEqual(status, 400)


class WikiInitUiLanguageTest(ServerWikiTest):
    """Task 6.15 step 1: /api/wiki/init writes the request's X-Kit-Lang
    into config.json as ui_language, independent of the wiki's own
    content `language`."""

    def test_install_request_in_korean_writes_ui_language_ko(self):
        wiki_path = self.home / "llm-wiki-ko"
        status, _body = self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "en", "targets": []},
            lang="ko",
        )
        self.assertEqual(status, 200)
        config = json.loads((self.home / ".keel" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["ui_language"], "ko")
        self.assertEqual(config["language"], "en")

    def test_install_request_in_english_writes_ui_language_en(self):
        wiki_path = self.home / "llm-wiki-en"
        status, _body = self._req(
            "/api/wiki/init",
            method="POST",
            body={"path": str(wiki_path), "language": "ko", "targets": []},
            lang="en",
        )
        self.assertEqual(status, 200)
        config = json.loads((self.home / ".keel" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["ui_language"], "en")


if __name__ == "__main__":
    unittest.main()
