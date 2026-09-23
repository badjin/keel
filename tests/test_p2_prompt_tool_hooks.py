from __future__ import annotations
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS_SRC = ROOT / "kit" / "hooks"


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


class PromptToolHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit_home = Path(self.tmp.name) / "kit_home"
        self.hooks_dir = self.kit_home / "hooks"
        self.hooks_dir.mkdir(parents=True)
        for name in (
            "_common.py",
            "wiki_trigger.py",
            "long_prompt_brief.py",
            "main_branch_guard.py",
            "wiki_read_log.py",
        ):
            shutil.copy(HOOKS_SRC / name, self.hooks_dir / name)

    def run_hook(self, script_name: str, payload: dict | str):
        script = self.hooks_dir / script_name
        stdin_data = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(
            [sys.executable, str(script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def write_config(self, wiki_path: Path, language: str = "ko", ui_language: str | None = None):
        self.kit_home.mkdir(parents=True, exist_ok=True)
        data = {"wiki_path": str(wiki_path), "language": language}
        data["ui_language"] = ui_language if ui_language is not None else language
        (self.kit_home / "config.json").write_text(json.dumps(data), encoding="utf-8")

    # wiki_trigger

    def test_wiki_trigger_fires_ko(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        proc = self.run_hook("wiki_trigger.py", {"prompt": "이거 위키에 저장해 줘"})
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertIn("kb-ingest", data["hookSpecificOutput"]["additionalContext"])

    def test_wiki_trigger_fires_en(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        proc = self.run_hook("wiki_trigger.py", {"prompt": "please update the wiki"})
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertIn("kb-ingest", data["hookSpecificOutput"]["additionalContext"])

    def test_wiki_trigger_silent_on_question(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        proc = self.run_hook("wiki_trigger.py", {"prompt": "위키가 뭐야?"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_wiki_trigger_silent_without_config(self):
        proc = self.run_hook("wiki_trigger.py", {"prompt": "이거 위키에 저장해 줘"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    # long_prompt_brief

    def test_long_prompt_brief_fires_at_200(self):
        prompt = "가" * 200
        proc = self.run_hook("long_prompt_brief.py", {"prompt": prompt})
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertIn("JOB", data["hookSpecificOutput"]["additionalContext"])

    def test_long_prompt_brief_silent_at_199(self):
        prompt = "가" * 199
        proc = self.run_hook("long_prompt_brief.py", {"prompt": prompt})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_long_prompt_brief_works_without_config(self):
        prompt = "가" * 200
        proc = self.run_hook("long_prompt_brief.py", {"prompt": prompt})
        self.assertEqual(proc.returncode, 0)
        self.assertNotEqual(proc.stdout.strip(), "")

    # main_branch_guard

    def _init_repo(self, branch: str) -> Path:
        repo = Path(self.tmp.name) / f"repo-{branch.replace('/', '-')}"
        repo.mkdir()
        _git(repo, "init", "-b", branch)
        _git(repo, "config", "user.email", "a@b.com")
        _git(repo, "config", "user.name", "a")
        (repo / "a.txt").write_text("hi", encoding="utf-8")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-m", "init")
        return repo

    def test_main_branch_guard_denies_on_main(self):
        repo = self._init_repo("main")
        target = repo / "a.txt"
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(target)},
            "cwd": str(repo),
        }
        proc = self.run_hook("main_branch_guard.py", payload)
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout)
        self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_main_branch_guard_denies_codex_apply_patch_on_main(self):
        repo = self._init_repo("main")
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {
                "input": "*** Begin Patch\n*** Update File: a.txt\n@@\n-hi\n+bye\n*** End Patch\n"
            },
            "cwd": str(repo),
        }
        proc = self.run_hook("main_branch_guard.py", payload)
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout)
        self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_main_branch_guard_allows_on_feature_branch(self):
        repo = self._init_repo("feat/x")
        target = repo / "a.txt"
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(target)},
            "cwd": str(repo),
        }
        proc = self.run_hook("main_branch_guard.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_main_branch_guard_silent_outside_repo(self):
        outside = Path(self.tmp.name) / "no-repo"
        outside.mkdir()
        target = outside / "a.txt"
        target.write_text("x", encoding="utf-8")
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(target)},
            "cwd": str(outside),
        }
        proc = self.run_hook("main_branch_guard.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_main_branch_guard_works_without_config(self):
        # No config.json anywhere in kit_home; guard must still function.
        repo = self._init_repo("main")
        target = repo / "a.txt"
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(target)},
            "cwd": str(repo),
        }
        proc = self.run_hook("main_branch_guard.py", payload)
        self.assertEqual(proc.returncode, 2)

    # wiki_read_log

    def test_wiki_read_log_writes_for_read(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        target = wiki / "a.md"
        target.write_text("x", encoding="utf-8")
        self.write_config(wiki)
        payload = {
            "session_id": "s1",
            "tool_name": "Read",
            "tool_input": {"file_path": str(target)},
        }
        proc = self.run_hook("wiki_read_log.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        log = (self.kit_home / "state" / "wiki-reads.log").read_text(encoding="utf-8")
        self.assertIn(str(target), log)
        self.assertIn("s1", log)

    def test_wiki_read_log_writes_for_codex_cat(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        target = wiki / "a.md"
        target.write_text("x", encoding="utf-8")
        self.write_config(wiki)
        payload = {
            "session_id": "s2",
            "tool_name": "exec_command",
            "tool_input": {"cmd": f"cat {target}"},
        }
        proc = self.run_hook("wiki_read_log.py", payload)
        self.assertEqual(proc.returncode, 0)
        log = (self.kit_home / "state" / "wiki-reads.log").read_text(encoding="utf-8")
        self.assertIn(str(target), log)

    def test_wiki_read_log_silent_outside_wiki(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        outside = Path(self.tmp.name) / "a.md"
        outside.write_text("x", encoding="utf-8")
        payload = {
            "session_id": "s3",
            "tool_name": "Read",
            "tool_input": {"file_path": str(outside)},
        }
        proc = self.run_hook("wiki_read_log.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse((self.kit_home / "state" / "wiki-reads.log").exists())

    def test_wiki_read_log_silent_for_piped_command(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        target = wiki / "a.md"
        target.write_text("x", encoding="utf-8")
        self.write_config(wiki)
        payload = {
            "session_id": "s4",
            "tool_name": "Bash",
            "tool_input": {"command": f"cat {target} | grep b"},
        }
        proc = self.run_hook("wiki_read_log.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse((self.kit_home / "state" / "wiki-reads.log").exists())

    def test_wiki_read_log_silent_without_config(self):
        target = Path(self.tmp.name) / "a.md"
        target.write_text("x", encoding="utf-8")
        payload = {
            "session_id": "s5",
            "tool_name": "Read",
            "tool_input": {"file_path": str(target)},
        }
        proc = self.run_hook("wiki_read_log.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
