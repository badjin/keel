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


class StopEndHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit_home = Path(self.tmp.name) / "kit_home"
        self.hooks_dir = self.kit_home / "hooks"
        self.hooks_dir.mkdir(parents=True)
        for name in (
            "_common.py",
            "ask_after_wiki.py",
            "verify_before_done.py",
            "session_capture.py",
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

    def _claude_row(self, role, text=None, tools=None):
        content = []
        if text is not None:
            content.append({"type": "text", "text": text})
        for name, tool_input in (tools or []):
            content.append({"type": "tool_use", "name": name, "input": tool_input})
        return {"type": role, "message": {"role": role, "content": content}}

    def _write_transcript(self, rows) -> Path:
        p = Path(self.tmp.name) / f"t_{id(rows)}.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return p

    def _codex_row_user(self, text):
        return {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        }

    def _codex_row_assistant(self, text):
        return {
            "type": "response_item",
            "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]},
        }

    # ask_after_wiki

    def test_ask_after_wiki_blocks_without_wiki_read(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        rows = [
            self._claude_row("user", "설정 문의"),
            self._claude_row("assistant", "어느 서버를 쓰나요?"),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "s1", "transcript_path": str(transcript)}
        proc = self.run_hook("ask_after_wiki.py", payload)
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout)
        self.assertEqual(data["decision"], "block")

        # second call for same turn: silent (marker already written)
        proc2 = self.run_hook("ask_after_wiki.py", payload)
        self.assertEqual(proc2.returncode, 0)
        self.assertEqual(proc2.stdout.strip(), "")

    def test_ask_after_wiki_silent_when_wiki_read(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        (wiki / "a.md").write_text("x", encoding="utf-8")
        self.write_config(wiki)
        rows = [
            self._claude_row("user", "설정 문의"),
            self._claude_row(
                "assistant", "어느 서버를 쓰나요?",
                tools=[("Read", {"file_path": str(wiki / "a.md")})],
            ),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "s2", "transcript_path": str(transcript)}
        proc = self.run_hook("ask_after_wiki.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_ask_after_wiki_silent_when_not_a_question(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        rows = [
            self._claude_row("user", "설정 문의"),
            self._claude_row("assistant", "설정을 확인했습니다."),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "s3", "transcript_path": str(transcript)}
        proc = self.run_hook("ask_after_wiki.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_ask_after_wiki_silent_without_config(self):
        rows = [
            self._claude_row("user", "설정 문의"),
            self._claude_row("assistant", "어느 서버를 쓰나요?"),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "s4", "transcript_path": str(transcript)}
        proc = self.run_hook("ask_after_wiki.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    # verify_before_done

    def test_verify_before_done_blocks_on_edit_without_verification(self):
        rows = [
            self._claude_row("user", "버그 고쳐줘"),
            self._claude_row(
                "assistant", "완료했습니다.",
                tools=[("Edit", {"file_path": "/w/a.py"})],
            ),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "s5", "transcript_path": str(transcript)}
        proc = self.run_hook("verify_before_done.py", payload)
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout)
        self.assertEqual(data["decision"], "block")

    def test_verify_before_done_silent_when_verified(self):
        rows = [
            self._claude_row("user", "버그 고쳐줘"),
            self._claude_row(
                "assistant", "완료했습니다.",
                tools=[
                    ("Edit", {"file_path": "/w/a.py"}),
                    ("Bash", {"command": "python3 -m unittest discover"}),
                ],
            ),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "s6", "transcript_path": str(transcript)}
        proc = self.run_hook("verify_before_done.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_verify_before_done_silent_without_edits(self):
        rows = [
            self._claude_row("user", "질문 있어요"),
            self._claude_row("assistant", "완료했습니다."),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "s7", "transcript_path": str(transcript)}
        proc = self.run_hook("verify_before_done.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_verify_before_done_works_without_config(self):
        rows = [
            self._claude_row("user", "버그 고쳐줘"),
            self._claude_row(
                "assistant", "완료했습니다.",
                tools=[("Edit", {"file_path": "/w/a.py"})],
            ),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "s8", "transcript_path": str(transcript)}
        proc = self.run_hook("verify_before_done.py", payload)
        self.assertEqual(proc.returncode, 2)

    # session_capture

    def test_session_capture_writes_claude(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        rows = [
            self._claude_row("user", "첫 질문"),
            self._claude_row("assistant", "첫 답"),
            self._claude_row("user", "둘째 질문"),
            self._claude_row("assistant", "마지막 답입니다"),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "abcdefgh1234", "transcript_path": str(transcript), "cwd": "/tmp/proj"}
        proc = self.run_hook("session_capture.py", payload)
        self.assertEqual(proc.returncode, 0)

        files = list((wiki / "raw" / "sessions").glob("*.md"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("status: unprocessed", content)
        self.assertIn("cli: claude", content)
        self.assertIn("마지막 답입니다", content)

    def test_session_capture_writes_codex(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        rows = [
            self._codex_row_user("첫 질문"),
            self._codex_row_assistant("첫 답"),
            self._codex_row_user("둘째 질문"),
            self._codex_row_assistant("마지막 답"),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "codexsid1", "transcript_path": str(transcript), "cwd": "/tmp/proj"}
        proc = self.run_hook("session_capture.py", payload)
        self.assertEqual(proc.returncode, 0)

        files = list((wiki / "raw" / "sessions").glob("*.md"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("cli: codex", content)

    def test_session_capture_silent_for_single_message(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        rows = [
            self._claude_row("user", "질문 하나"),
            self._claude_row("assistant", "답"),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "onlyone1", "transcript_path": str(transcript), "cwd": "/tmp/proj"}
        proc = self.run_hook("session_capture.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertFalse((wiki / "raw" / "sessions").exists())

    def test_session_capture_suffixes_existing_filename(self):
        wiki = Path(self.tmp.name) / "wiki"
        wiki.mkdir()
        self.write_config(wiki)
        rows = [
            self._claude_row("user", "첫 질문"),
            self._claude_row("assistant", "첫 답"),
            self._claude_row("user", "둘째 질문"),
            self._claude_row("assistant", "답 1"),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "sameid12", "transcript_path": str(transcript), "cwd": "/tmp/proj"}
        proc1 = self.run_hook("session_capture.py", payload)
        self.assertEqual(proc1.returncode, 0)

        rows2 = [
            self._claude_row("user", "셋째 질문"),
            self._claude_row("assistant", "답 2"),
            self._claude_row("user", "넷째 질문"),
            self._claude_row("assistant", "답 3"),
        ]
        transcript2 = self._write_transcript(rows2)
        payload2 = {"session_id": "sameid12", "transcript_path": str(transcript2), "cwd": "/tmp/proj"}
        proc2 = self.run_hook("session_capture.py", payload2)
        self.assertEqual(proc2.returncode, 0)

        files = list((wiki / "raw" / "sessions").glob("*.md"))
        self.assertEqual(len(files), 2)
        names = sorted(f.name for f in files)
        self.assertTrue(any(n.endswith("-2.md") for n in names))

    def test_session_capture_silent_without_config(self):
        rows = [
            self._claude_row("user", "첫 질문"),
            self._claude_row("assistant", "첫 답"),
            self._claude_row("user", "둘째 질문"),
            self._claude_row("assistant", "답"),
        ]
        transcript = self._write_transcript(rows)
        payload = {"session_id": "noconfig1", "transcript_path": str(transcript), "cwd": "/tmp/proj"}
        proc = self.run_hook("session_capture.py", payload)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
