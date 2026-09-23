from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "kit" / "hooks"))

from kit import install_hooks
import handoff_common as hc
import handoff_auto
import handoff_gate
import handoff_restore


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"KEEL_HOME": str(self.home), "HERDR_ENV": "", "HERDR_PANE_ID": "", "TMUX": "", "TMUX_PANE": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_install_wraps_and_restores_existing_statusline(self):
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.parent.mkdir()
        original = {"type": "command", "command": "cat"}
        settings_path.write_text(json.dumps({"statusLine": original}))
        install_hooks.install(self.home, ROOT, ["automatic-handoff"], ["claude"],
                              handoff_threshold=60, handoff_targets=["claude"])
        settings = json.loads(settings_path.read_text())
        self.assertIn("handoff_statusline.py", settings["statusLine"]["command"])
        self.assertTrue((self.home / ".claude" / "skills" / "handoff" / "SKILL.md").exists())
        self.assertEqual(hc.read_json(self.home / ".keel" / "state" / "statusline.json")["claude"], original)
        install_hooks.uninstall(self.home, ["claude"])
        settings = json.loads(settings_path.read_text())
        self.assertEqual(settings["statusLine"], original)
        self.assertFalse((self.home / ".claude" / "skills" / "handoff" / "SKILL.md").exists())

    def test_statusline_records_usage_and_preserves_output(self):
        hc.write_json(self.home / ".keel" / "state" / "statusline.json",
                      {"claude": {"type": "command", "command": "cat"}})
        payload = b'{"session_id":"abc","context_window":{"used_percentage":61,"context_window_size":1000}}'
        result = subprocess.run([sys.executable, str(ROOT / "kit" / "hooks" / "handoff_statusline.py"), "claude"],
                                input=payload, capture_output=True, env=os.environ.copy(), check=True)
        self.assertEqual(result.stdout, payload)
        self.assertEqual(hc.read_json(self.home / ".keel" / "state" / "context" / "abc.json")["used_percentage"], 61)

    def test_grok_statusline_toml_is_restored(self):
        path = self.home / ".grok" / "config.toml"
        path.parent.mkdir()
        original = '[ui.status_line]\ntype = "command"\ncommand = "cat"\n\n[other]\nvalue = 1\n'
        path.write_text(original)
        install_hooks.install(self.home, ROOT, ["automatic-handoff"], ["grok"], handoff_targets=["grok"])
        self.assertIn("handoff_statusline.py", path.read_text())
        self.assertEqual(hc.read_json(self.home / ".keel" / "state" / "statusline.json")["grok"]["command"], "cat")
        install_hooks.uninstall(self.home, ["grok"])
        self.assertEqual(path.read_text(), original)

    def test_codex_usage_and_gate_rearm(self):
        rollout = self.home / "rollout.jsonl"
        rollout.write_text(json.dumps({"payload": {"model_context_window": 1000,
                                                    "last_token_usage": {"total_tokens": 610}}}) + "\n")
        self.assertEqual(hc.usage("abc", str(rollout), "codex"), 61)
        payload = {"session_id": "abc", "cwd": str(self.home), "transcript_path": str(rollout)}
        with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), patch.object(sys, "argv", ["gate", "codex"]), patch.object(sys, "stdout", new_callable=io.StringIO) as output:
            handoff_gate.run()
            self.assertEqual(json.loads(output.getvalue())["decision"], "block")
        with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), patch.object(sys, "argv", ["gate", "codex"]), patch.object(sys, "stdout", new_callable=io.StringIO) as output:
            handoff_gate.run()
            self.assertEqual(output.getvalue(), "")
        rollout.write_text(json.dumps({"model_context_window": 1000,
                                      "last_token_usage": {"total_tokens": 770}}) + "\n")
        with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), patch.object(sys, "argv", ["gate", "codex"]), patch.object(sys, "stdout", new_callable=io.StringIO) as output:
            handoff_gate.run()
            self.assertEqual(json.loads(output.getvalue())["decision"], "block")

    def test_grok_camel_case_payload_uses_statusline_state(self):
        hc.write_json(self.home / ".keel" / "config.json", {"handoff_targets": ["grok"]})
        hc.write_json(self.home / ".keel" / "state" / "context" / "grok-1.json",
                      {"used_percentage": 65})
        payload = {"sessionId": "grok-1", "cwd": str(self.home), "reason": "end_turn"}
        with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), patch.object(sys, "argv", ["gate", "grok"]), patch.object(sys, "stdout", new_callable=io.StringIO) as output:
            handoff_gate.run()
            self.assertEqual(json.loads(output.getvalue())["decision"], "block")

    def test_marker_restore_claim_and_grace(self):
        cwd = self.home / "project"
        handoff_dir = cwd / ".keel" / "handoff"
        handoff_dir.mkdir(parents=True)
        handoff = handoff_dir / "handoff-1.md"
        handoff.write_text("work")
        marker, automatic = handoff_auto.start(handoff, "old", "codex", cwd)
        self.assertFalse(automatic)
        self.assertEqual(hc.marker_for(str(cwd), "codex")[0], marker)
        with patch.object(sys, "stdin", io.StringIO(json.dumps({"session_id": "new", "cwd": str(cwd)}))), patch.object(sys, "argv", ["restore", "codex"]), patch.object(sys, "stdout", new_callable=io.StringIO):
            handoff_restore.run()
        self.assertTrue(Path(str(marker) + ".claimed").exists())
        self.assertGreater(hc.read_json(self.home / ".keel" / "state" / "context-handoff" / "new.json")["grace_until"], 0)


if __name__ == "__main__":
    unittest.main()
