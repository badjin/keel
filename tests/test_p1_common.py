from __future__ import annotations
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS_SRC = ROOT / "kit" / "hooks"
COMMON_PATH = HOOKS_SRC / "_common.py"
NO_SPECULATION_PATH = HOOKS_SRC / "no_speculation.py"


def load_common():
    spec = importlib.util.spec_from_file_location("_common_under_test", COMMON_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CommonTest(unittest.TestCase):
    def setUp(self):
        self.common = load_common()

    def test_normalize_tool_exec_command(self):
        c = self.common
        self.assertEqual(
            c.normalize_tool("functions.exec_command", {"cmd": "ls"}),
            [("Bash", {"command": "ls"})],
        )

    def test_normalize_tool_apply_patch(self):
        c = self.common
        patch_text = (
            "*** Begin Patch\n"
            "*** Add File: a.txt\n"
            "+hi\n"
            "*** Update File: b/c.py\n"
            "@@\n"
            "-x\n"
            "+y\n"
            "*** End Patch\n"
        )
        result = c.normalize_tool("apply_patch", {"input": patch_text}, cwd="/tmp/r")
        self.assertEqual(
            result,
            [
                ("Write", {"file_path": "/tmp/r/a.txt"}),
                ("Edit", {"file_path": "/tmp/r/b/c.py"}),
            ],
        )

    def test_normalize_tool_passthrough(self):
        c = self.common
        self.assertEqual(c.normalize_tool("Edit", {"file_path": "/x"}), [("Edit", {"file_path": "/x"})])

    def test_normalize_tool_shell_command_list(self):
        c = self.common
        self.assertEqual(
            c.normalize_tool("shell", {"command": ["bash", "-lc", "ls -la"]}),
            [("Bash", {"command": "ls -la"})],
        )
        self.assertEqual(
            c.normalize_tool("shell", {"command": ["git", "status"]}),
            [("Bash", {"command": "git status"})],
        )

    def test_read_transcript_codex_custom_tool_call_apply_patch_uses_cwd(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            patch_text = (
                "*** Begin Patch\n"
                "*** Update File: src/a.py\n"
                "@@\n"
                "-x\n"
                "+y\n"
                "*** End Patch\n"
            )
            rows = [
                {"type": "session_meta", "payload": {"cwd": "/tmp/r"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "apply_patch",
                        "input": patch_text,
                        "call_id": "c2",
                    },
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            msgs = c.read_transcript(str(p))
            all_tools = [t for m in msgs for t in m["tools"]]
            self.assertIn(("Edit", {"file_path": "/tmp/r/src/a.py"}), all_tools)

    def test_read_transcript_codex_item_completed_command_execution(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {
                            "type": "CommandExecution",
                            "id": "i1",
                            "command": ["bash", "-lc", "python3 -m unittest"],
                            "exit_code": 0,
                        },
                    },
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            msgs = c.read_transcript(str(p))
            all_tools = [t for m in msgs for t in m["tools"]]
            self.assertIn(("Bash", {"command": "python3 -m unittest"}), all_tools)

    def test_read_transcript_claude(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            rows = [
                {"type": "user", "message": {"role": "user", "content": "질문"}},
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "답"},
                            {"type": "tool_use", "name": "Read", "input": {"file_path": "/w/a.md"}},
                        ],
                    },
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            msgs = c.read_transcript(str(p))
            self.assertEqual(len(msgs), 2)
            tools = msgs[1]["tools"]
            self.assertEqual(tools, [("Read", {"file_path": "/w/a.md"})])

    def test_read_transcript_codex(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "질문"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "cat /w/a.md"}),
                        "call_id": "c1",
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "끝"}],
                    },
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            msgs = c.read_transcript(str(p))
            roles = [m["role"] for m in msgs]
            self.assertEqual(roles, ["user", "assistant", "assistant"])
            last = c.last_turn(msgs)
            all_tools = [t for m in last for t in m["tools"]]
            self.assertIn(("Bash", {"command": "cat /w/a.md"}), all_tools)
            self.assertEqual(c.last_assistant_text(msgs), "끝")

    def test_read_transcript_missing(self):
        c = self.common
        self.assertEqual(c.read_transcript("/nonexistent/path/xyz.jsonl"), [])

    def test_may_block_stop(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            kh = Path(td)
            self.assertFalse(c.may_block_stop({"stop_hook_active": True}, "x", kh))
            payload = {"session_id": "s", "transcript_path": str(kh / "nope.jsonl")}
            self.assertTrue(c.may_block_stop(payload, "x", kh))
            self.assertFalse(c.may_block_stop(payload, "x", kh))

    def test_read_transcript_skips_synthetic_claude_user_rows(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "<local-command-caveat>Caveat: ...</local-command-caveat>",
                    },
                },
                {
                    "type": "user",
                    "message": {"role": "user", "content": "<command-name>/model</command-name>"},
                },
                {"type": "user", "message": {"role": "user", "content": "질문"}},
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            msgs = c.read_transcript(str(p))
            user_msgs = [m for m in msgs if m["role"] == "user"]
            self.assertEqual(len(user_msgs), 1)
            self.assertEqual(user_msgs[0]["text"], "질문")

    def test_read_transcript_skips_synthetic_codex_user_rows(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "<environment_context>\n  <cwd>/x</cwd>\n</environment_context>",
                            }
                        ],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "# AGENTS.md instructions for /x\n...",
                            }
                        ],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "질문"}],
                    },
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            msgs = c.read_transcript(str(p))
            user_msgs = [m for m in msgs if m["role"] == "user"]
            self.assertEqual(len(user_msgs), 1)
            self.assertEqual(user_msgs[0]["text"], "질문")

    def test_block_stop_and_deny_tool_prefix(self):
        c = self.common
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            c.block_stop("abc")
        data = json.loads(buf.getvalue())
        self.assertTrue(data["reason"].startswith("[keel] "))

        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            c.deny_tool("xyz")
        data2 = json.loads(buf2.getvalue())
        self.assertTrue(
            data2["hookSpecificOutput"]["permissionDecisionReason"].startswith("[keel] ")
        )

    def test_may_block_stop_false_on_kit_prefixed_feedback(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            kh = Path(td)
            transcript = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "user",
                    "message": {"role": "user", "content": "[keel] 확인하고 다시 하세요"},
                },
            ]
            transcript.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            payload = {"session_id": "fresh-session", "transcript_path": str(transcript)}
            self.assertFalse(c.may_block_stop(payload, "x", kh))

    def test_read_transcript_keeps_real_text_part_alongside_synthetic(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<system-reminder>\nctx\n</system-reminder>"},
                            {"type": "text", "text": "진짜 질문"},
                        ],
                    },
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            msgs = c.read_transcript(str(p))
            user_msgs = [m for m in msgs if m["role"] == "user"]
            self.assertEqual(len(user_msgs), 1)
            self.assertEqual(user_msgs[0]["text"], "진짜 질문")

    def test_read_transcript_skips_row_whose_only_part_is_synthetic(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "<system-reminder>\nctx\n</system-reminder>"},
                        ],
                    },
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            msgs = c.read_transcript(str(p))
            user_msgs = [m for m in msgs if m["role"] == "user"]
            self.assertEqual(len(user_msgs), 0)

    def test_read_transcript_skips_plain_string_command_name(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "user",
                    "message": {"role": "user", "content": "<command-name>/x</command-name>"},
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            msgs = c.read_transcript(str(p))
            user_msgs = [m for m in msgs if m["role"] == "user"]
            self.assertEqual(len(user_msgs), 0)

    def test_may_block_stop_false_on_kit_prefixed_feedback_with_leading_spaces(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            kh = Path(td)
            transcript = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "user",
                    "message": {"role": "user", "content": "  [keel] 확인하고 다시 하세요"},
                },
            ]
            transcript.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            payload = {"session_id": "fresh-session-2", "transcript_path": str(transcript)}
            self.assertFalse(c.may_block_stop(payload, "x", kh))

    def test_load_config(self):
        c = self.common
        with tempfile.TemporaryDirectory() as td:
            kh = Path(td)
            self.assertIsNone(c.load_config(kh))
            (kh / "config.json").write_text(
                json.dumps({"wiki_path": str(kh / "wiki"), "language": "ko"}), encoding="utf-8"
            )
            cfg = c.load_config(kh)
            self.assertIsInstance(cfg, dict)
            self.assertEqual(cfg["language"], "ko")

    def test_main_guard_suppressed_by_env_returns_0_and_prints_nothing(self):
        # Uses a temporary kit-home copy of the hook (not kit/hooks/ in the
        # repo) so a would-be may_block_stop() state write never lands under
        # the repo's kit/ — matching the isolation pattern in test_p1_hooks.py.
        with tempfile.TemporaryDirectory() as td:
            kit_home = Path(td) / "kit_home"
            hooks_dir = kit_home / "hooks"
            hooks_dir.mkdir(parents=True)
            for name in ("_common.py", "no_speculation.py"):
                shutil.copy(HOOKS_SRC / name, hooks_dir / name)
            no_speculation_copy = hooks_dir / "no_speculation.py"

            p = Path(td) / "t.jsonl"
            rows = [
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "아마도 그럴 것 같습니다"}]},
                },
            ]
            p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            payload = json.dumps({"transcript_path": str(p)})

            env = dict(os.environ)
            env["LLM_WIKI_KIT_SUPPRESS"] = "1"
            result = subprocess.run(
                [sys.executable, str(no_speculation_copy)],
                input=payload,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertFalse((kit_home / "state").exists())


if __name__ == "__main__":
    unittest.main()
