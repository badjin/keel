from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kit.workflow import approval, docs, fsutil, work


SOURCE_KIT = Path(__file__).resolve().parents[1] / "kit"


class WorkflowHooksTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.keel_home = root / "home" / ".keel"
        self.kb = root / "kb"
        self.hooks = self.keel_home / "workflow-hooks"
        self.hooks.mkdir(parents=True)
        shutil.copy2(SOURCE_KIT / "hooks" / "_common.py", self.hooks)
        for name in ("wf_prompt.py", "wf_track.py"):
            shutil.copy2(SOURCE_KIT / "workflow" / "hooks" / name, self.hooks)
        shutil.copytree(SOURCE_KIT / "workflow", self.keel_home / "workflow")
        self.config = {
            "wiki_path": str(self.kb),
            "workflow": {"installed": True, "language": "en", "keel_wf": "keel-wf"},
        }
        self.write_config()
        self.folder = work.new_work(self.kb, self.keel_home, "example", "en", str(root), "2026-09-28")
        intent = self.folder / "intent.md"
        intent.write_text(intent.read_text(encoding="utf-8")
                          .replace("## Summary\n", "## Summary\nProvide workflow hook context.\n")
                          .replace("## Problem\n", "## Problem\nA workflow hook needs context.\n")
                          .replace("## Proposed outcome\n", "## Proposed outcome\n- Provide context. [Q1]\n")
                          .replace("## Knowledge base consulted\n",
                                   "## Knowledge base consulted\n- `index.md` — Nothing on this yet.\n")
                          .replace("## Constraints\n", "## Constraints\n- Keep the hook scoped. [Q1]\n")
                          .replace("## Open questions\n", "## Open questions\nNone.\n")
                          .replace("## Quotes\n", "## Quotes\n**Q1** Provide workflow hook context.\n"),
                          encoding="utf-8")
        self.root = root

    def write_config(self):
        (self.keel_home / "config.json").write_text(json.dumps(self.config), encoding="utf-8")

    def call(self, name, payload, *, target="codex", nested=False):
        env = os.environ.copy()
        env.pop("LLM_WIKI_KIT_NESTED", None)
        if nested:
            env["LLM_WIKI_KIT_NESTED"] = "1"
        return subprocess.run(
            [sys.executable, str(self.hooks / name), target], input=json.dumps(payload),
            text=True, capture_output=True, env=env, check=True,
        )

    def awaiting(self):
        intent = self.folder / "intent.md"
        intent.write_text(docs.set_status(intent.read_text(encoding="utf-8"), "awaiting-approval"), encoding="utf-8")
        state = work.load_state(self.folder)
        state["reviews"]["intent"] = {
            "verdict": "PASS", "hashes": {"intent": docs.intent_hash(intent.read_text(encoding="utf-8"))},
        }
        fsutil.atomic_write_json(self.folder / "state.json", state)

    def test_prompt_approval_records_ledger_and_status(self):
        self.awaiting()
        result = self.call("wf_prompt.py", {"prompt": "approve example", "session_id": "s1"})
        context = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "UserPromptSubmit")
        self.assertTrue(context["additionalContext"])
        self.assertEqual(docs.get_status((self.folder / "intent.md").read_text(encoding="utf-8")), "approved")
        ledger = approval.ledger_path(self.keel_home)
        self.assertEqual(json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])["session"], "s1")

    def test_nested_prompt_is_silent_and_does_not_approve(self):
        self.awaiting()
        result = self.call("wf_prompt.py", {"prompt": "approve example", "session_id": "s1"}, nested=True)
        self.assertEqual(result.stdout, "")
        self.assertFalse(approval.ledger_path(self.keel_home).exists())

    def test_uninstalled_and_synthetic_prompts_are_silent(self):
        self.config["workflow"]["installed"] = False
        self.write_config()
        self.assertEqual(self.call("wf_prompt.py", {"prompt": "Hello"}).stdout, "")
        self.config["workflow"]["installed"] = True
        self.write_config()
        self.assertEqual(self.call("wf_prompt.py", {"prompt": "<environment_context> synthetic"}).stdout, "")

    def test_track_read_and_bash_add_sessions(self):
        payload = {"session_id": "s1", "transcript_path": "/tmp/one.jsonl", "cwd": str(self.root),
                   "tool_name": "Read", "tool_input": {"file_path": str(self.folder / "intent.md")}}
        self.assertEqual(self.call("wf_track.py", payload).stdout, "")
        session = self.keel_home / "state" / "workflow" / "sessions" / "s1.json"
        self.assertEqual(json.loads(session.read_text(encoding="utf-8"))["folder"], str(self.folder))
        payload.update(session_id="s2", transcript_path="/tmp/two.jsonl", tool_name="Bash",
                       tool_input={"command": f"cat {self.folder / 'spec.md'}"})
        self.assertEqual(self.call("wf_track.py", payload).stdout, "")
        sessions = work.load_state(self.folder)["sessions"]
        self.assertEqual([(item["session"], item["transcript_path"]) for item in sessions],
                         [("s1", "/tmp/one.jsonl"), ("s2", "/tmp/two.jsonl")])

    def test_owned_draft_emits_context_with_folder(self):
        sessions = self.keel_home / "state" / "workflow" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "s1.json").write_text(json.dumps({"folder": str(self.folder)}), encoding="utf-8")
        result = self.call("wf_prompt.py", {"prompt": "Continue", "session_id": "s1"})
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(self.folder), context)
        self.assertIn("critic intent", context)
