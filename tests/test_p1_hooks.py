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


class HooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit_home = Path(self.tmp.name) / "kit_home"
        self.hooks_dir = self.kit_home / "hooks"
        self.hooks_dir.mkdir(parents=True)
        for name in ("_common.py", "_maintenance.py", "_wiki_guard.py", "wiki_loader.py", "no_speculation.py"):
            shutil.copy(HOOKS_SRC / name, self.hooks_dir / name)

    def run_hook(self, script_name: str, payload: dict | str):
        script = self.hooks_dir / script_name
        if isinstance(payload, str):
            stdin_data = payload
        else:
            stdin_data = json.dumps(payload)
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc

    def write_config(self, wiki_path: Path, language: str = "ko", ui_language: str | None = None):
        self.kit_home.mkdir(parents=True, exist_ok=True)
        data = {"wiki_path": str(wiki_path), "language": language}
        data["ui_language"] = ui_language if ui_language is not None else language
        (self.kit_home / "config.json").write_text(json.dumps(data), encoding="utf-8")

    def make_claude_transcript(self, last_assistant_text: str) -> Path:
        p = Path(self.tmp.name) / "t.jsonl"
        rows = [
            {"type": "user", "message": {"role": "user", "content": "question"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": last_assistant_text}],
                },
            },
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return p

    def make_codex_transcript(self, last_assistant_text: str) -> Path:
        p = Path(self.tmp.name) / "t_codex.jsonl"
        rows = [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "question"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": last_assistant_text}],
                },
            },
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return p

    # wiki_loader

    def test_wiki_loader_no_config(self):
        proc = self.run_hook("wiki_loader.py", {"session_id": "s"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_wiki_loader_with_config(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text("# 내 위키", encoding="utf-8")
        self.write_config(wiki, "ko")
        proc = self.run_hook("wiki_loader.py", {"session_id": "s"})
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertIn("# 내 위키", ctx)

    def test_wiki_loader_notice_once(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text("# My KB\n## Topics", encoding="utf-8")
        self.write_config(wiki, "en")
        state = self.kit_home / "state"
        state.mkdir()
        notice_file = state / "auto-update-notice.json"
        notice_file.write_text(json.dumps({
            "kind": "rejected",
            "problem": "changed",
            "path": "wiki/page.md",
            "count": 1,
        }), encoding="utf-8")

        first = self.run_hook("wiki_loader.py", {"session_id": "s"})
        self.assertEqual(first.returncode, 0)
        data = json.loads(first.stdout)
        self.assertIn("wiki/page.md", data["systemMessage"])
        context = data["hookSpecificOutput"]["additionalContext"]
        self.assertIn(data["systemMessage"], context)
        self.assertIn("# My KB", context)
        self.assertFalse(notice_file.exists())

        second = self.run_hook("wiki_loader.py", {"session_id": "s"})
        self.assertEqual(second.returncode, 0)
        self.assertNotIn("systemMessage", json.loads(second.stdout))

    def test_wiki_loader_index_not_utf8(self):
        wiki = Path(self.tmp.name) / "wiki_bad"
        wiki.mkdir()
        (wiki / "index.md").write_bytes(b"\xff\xfe\x00bad")
        self.write_config(wiki, "ko")
        proc = self.run_hook("wiki_loader.py", {"session_id": "s"})
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(wiki), ctx)

    # no_speculation

    def test_no_speculation_blocks_then_silent(self):
        transcript = self.make_claude_transcript("아마 설정 문제일 겁니다.")
        payload = {"session_id": "s1", "transcript_path": str(transcript)}
        proc1 = self.run_hook("no_speculation.py", payload)
        self.assertEqual(proc1.returncode, 2)
        data = json.loads(proc1.stdout)
        self.assertEqual(data["decision"], "block")

        proc2 = self.run_hook("no_speculation.py", payload)
        self.assertEqual(proc2.returncode, 0)
        self.assertEqual(proc2.stdout.strip(), "")

    def test_no_speculation_stop_hook_active(self):
        transcript = self.make_claude_transcript("아마 그럴 겁니다.")
        payload = {"session_id": "s2", "transcript_path": str(transcript), "stop_hook_active": True}
        proc = self.run_hook("no_speculation.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_no_speculation_confirmed_fact(self):
        transcript = self.make_claude_transcript("확인했습니다. 설정 값이 X 입니다.")
        payload = {"session_id": "s3", "transcript_path": str(transcript)}
        proc = self.run_hook("no_speculation.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_no_speculation_fenced_block_ignored(self):
        text = "```\n아마 이런 코드일 것 같습니다\n```\n확인했습니다."
        transcript = self.make_claude_transcript(text)
        payload = {"session_id": "s4", "transcript_path": str(transcript)}
        proc = self.run_hook("no_speculation.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_no_speculation_reason_english_by_default(self):
        transcript = self.make_claude_transcript("probably fine")
        payload = {"session_id": "sx1", "transcript_path": str(transcript)}
        proc = self.run_hook("no_speculation.py", payload)
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout)
        self.assertEqual(data["decision"], "block")

    def test_no_speculation_codex_transcript(self):
        transcript = self.make_codex_transcript("probably fine")
        payload = {"session_id": "s5", "transcript_path": str(transcript)}
        proc = self.run_hook("no_speculation.py", payload)
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout)
        self.assertEqual(data["decision"], "block")

    def test_garbage_stdin(self):
        proc = self.run_hook("no_speculation.py", "not json")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

        proc2 = self.run_hook("wiki_loader.py", "not json")
        self.assertEqual(proc2.returncode, 0)
        self.assertEqual(proc2.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
