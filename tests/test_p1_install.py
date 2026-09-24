from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kit import install_hooks


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()

    def test_install_claude_and_codex(self):
        result = install_hooks.install(
            self.home, ROOT, ["wiki-loader", "no-speculation"], ["claude", "codex"]
        )
        self.assertEqual(set(result["claude"]), {"wiki-loader", "no-speculation"})
        self.assertEqual(set(result["codex"]), {"wiki-loader", "no-speculation"})

        settings = json.loads((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        session_start_cmds = [
            h["command"]
            for grp in settings["hooks"]["SessionStart"]
            for h in grp["hooks"]
        ]
        stop_cmds = [
            h["command"]
            for grp in settings["hooks"]["Stop"]
            for h in grp["hooks"]
        ]
        self.assertTrue(any(c.endswith('wiki_loader.py"') for c in session_start_cmds))
        self.assertTrue(any(c.endswith('no_speculation.py"') for c in stop_cmds))

        hooks_json = json.loads((self.home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        codex_session_start = [
            h["command"]
            for grp in hooks_json["hooks"]["SessionStart"]
            for h in grp["hooks"]
        ]
        codex_stop = [
            h["command"]
            for grp in hooks_json["hooks"]["Stop"]
            for h in grp["hooks"]
        ]
        self.assertTrue(any(c.endswith('wiki_loader.py"') for c in codex_session_start))
        self.assertTrue(any(c.endswith('no_speculation.py"') for c in codex_stop))

        config_toml = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn("[features]", config_toml)
        self.assertIn("hooks = true", config_toml)

        hooks_dir = self.home / ".keel" / "hooks"
        self.assertTrue((hooks_dir / "wiki_loader.py").exists())
        self.assertTrue((hooks_dir / "no_speculation.py").exists())
        self.assertTrue((hooks_dir / "_common.py").exists())

    def test_legacy_hook_is_replaced_on_install_and_removed_on_uninstall(self):
        settings_path = self.home / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        old = '/tmp/.llm-wiki-kit/hooks/wiki_loader.py'
        settings_path.write_text(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": old}]}
        ]}}), encoding="utf-8")
        install_hooks.install(self.home, ROOT, ["wiki-loader"], ["claude"])
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        commands = [h["command"] for g in settings["hooks"]["SessionStart"] for h in g["hooks"]]
        self.assertNotIn(old, commands)
        self.assertEqual(len(commands), 1)

        settings_path.write_text(json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": old}]}
        ]}}), encoding="utf-8")
        install_hooks.uninstall(self.home, ["claude"])
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(settings["hooks"], {})

    def test_install_preserves_foreign_and_is_idempotent(self):
        claude_dir = self.home / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text(
            json.dumps({
                "theme": "dark",
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "/usr/local/bin/mine.sh"}]}
                    ]
                },
            }),
            encoding="utf-8",
        )
        codex_dir = self.home / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "config.toml").write_text("[features]\nhooks = false\nother = 1\n", encoding="utf-8")

        install_hooks.install(self.home, ROOT, ["no-speculation"], ["claude", "codex"])
        install_hooks.install(self.home, ROOT, ["no-speculation"], ["claude", "codex"])

        settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["theme"], "dark")
        stop_cmds = [h["command"] for grp in settings["hooks"]["Stop"] for h in grp["hooks"]]
        self.assertEqual(stop_cmds.count("/usr/local/bin/mine.sh"), 1)
        kit_cmds = [c for c in stop_cmds if "/.keel/hooks/" in c]
        self.assertEqual(len(kit_cmds), 1)

        config_toml = (codex_dir / "config.toml").read_text(encoding="utf-8")
        self.assertEqual(config_toml.count("hooks = true"), 1)
        self.assertIn("other = 1", config_toml)

        backups = list(claude_dir.glob("settings.json.bak-*"))
        self.assertTrue(len(backups) >= 1)

    def test_uninstall_removes_only_kit(self):
        claude_dir = self.home / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text(
            json.dumps({
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "/usr/local/bin/mine.sh"}]}
                    ]
                }
            }),
            encoding="utf-8",
        )
        install_hooks.install(self.home, ROOT, ["no-speculation"], ["claude", "codex"])
        result = install_hooks.uninstall(self.home, ["claude", "codex"])
        self.assertGreaterEqual(result["claude"], 1)

        settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
        all_cmds = [
            h["command"]
            for evt in settings.get("hooks", {}).values()
            for grp in evt
            for h in grp["hooks"]
        ]
        self.assertFalse(any("/.keel/hooks/" in c for c in all_cmds))
        self.assertIn("/usr/local/bin/mine.sh", all_cmds)

    def test_uninstall_with_remove_skills_removes_kit_marked_skills_only(self):
        from kit.skills_install import install_skills

        wiki_path = self.home / "llm-wiki"
        install_skills(self.home, wiki_path, ["claude", "codex"])

        # A user-authored SKILL.md (no kit marker) must survive.
        user_skill = self.home / ".claude" / "skills" / "my-own" / "SKILL.md"
        user_skill.parent.mkdir(parents=True)
        user_skill.write_text("# not from the kit\n", encoding="utf-8")

        install_hooks.install(self.home, ROOT, ["no-speculation"], ["claude", "codex"])
        result = install_hooks.uninstall(self.home, ["claude", "codex"], remove_skills=True)

        for base in (".claude", ".codex"):
            for name in ("kb", "kb-ingest", "kb-lint", "kb-health"):
                skill_path = self.home / base / "skills" / name / "SKILL.md"
                self.assertFalse(skill_path.exists(), skill_path)
                self.assertFalse(skill_path.parent.exists(), skill_path.parent)

        self.assertTrue(user_skill.exists())
        self.assertEqual(user_skill.read_text(encoding="utf-8"), "# not from the kit\n")
        self.assertEqual(len(result["skills_removed"]), 8)

    def test_uninstall_without_remove_skills_leaves_skills_in_place(self):
        from kit.skills_install import install_skills

        wiki_path = self.home / "llm-wiki"
        install_skills(self.home, wiki_path, ["claude"])

        install_hooks.uninstall(self.home, ["claude"])

        skill_path = self.home / ".claude" / "skills" / "kb" / "SKILL.md"
        self.assertTrue(skill_path.exists())

    def test_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            install_hooks.install(self.home, ROOT, ["not-a-real-id"], ["claude"])

    def test_first_install_writes_bak_before_kit_sentinel(self):
        install_hooks.install(self.home, ROOT, ["no-speculation"], ["claude", "codex"])
        claude_sentinel = self.home / ".claude" / "settings.json.bak-before-kit"
        codex_sentinel = self.home / ".codex" / "hooks.json.bak-before-kit"
        self.assertTrue(claude_sentinel.exists())
        self.assertTrue(codex_sentinel.exists())
        self.assertEqual(json.loads(claude_sentinel.read_text(encoding="utf-8")), {})
        self.assertEqual(
            json.loads(codex_sentinel.read_text(encoding="utf-8")), {"hooks": {}}
        )

    def test_second_install_does_not_overwrite_sentinel(self):
        install_hooks.install(self.home, ROOT, ["no-speculation"], ["claude", "codex"])
        sentinel = self.home / ".claude" / "settings.json.bak-before-kit"
        sentinel.write_text('{"tampered": true}', encoding="utf-8")
        install_hooks.install(self.home, ROOT, ["wiki-loader"], ["claude", "codex"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"tampered": true}')

    def test_no_sentinel_written_when_file_already_existed(self):
        claude_dir = self.home / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text("{}", encoding="utf-8")
        install_hooks.install(self.home, ROOT, ["no-speculation"], ["claude"])
        sentinel = claude_dir / "settings.json.bak-before-kit"
        self.assertFalse(sentinel.exists())

    def test_installed_ids(self):
        install_hooks.install(self.home, ROOT, ["wiki-loader", "no-speculation"], ["claude", "codex"])
        ids = install_hooks.installed_ids(self.home)
        self.assertEqual(set(ids["claude"]), {"wiki-loader", "no-speculation"})
        self.assertEqual(set(ids["codex"]), {"wiki-loader", "no-speculation"})


class CodexTrustStatusTest(unittest.TestCase):
    """Task 6.18 step 2: given a sample config.toml + hooks.json in a temp
    home, codex_trust_status() reports the right trusted/untrusted split."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()

    def _hook_keys(self):
        """Reads the just-installed hooks.json back and returns
        {hook_id: 'event:group_idx:handler_idx'} for every kit-owned entry,
        the same way codex_trust_status derives its keys."""
        hooks_json_path = self.home / ".codex" / "hooks.json"
        hooks_json = json.loads(hooks_json_path.read_text(encoding="utf-8"))
        script_to_id = {
            h["script"]: h["id"]
            for h in install_hooks.load_catalogue(ROOT)
        }
        keys = {}
        for event, groups in hooks_json["hooks"].items():
            for group_idx, grp in enumerate(groups):
                for handler_idx, h in enumerate(grp["hooks"]):
                    script = h["command"].rsplit("/", 1)[-1].rstrip('"')
                    hook_id = script_to_id.get(script, script)
                    keys[hook_id] = f"{event}:{group_idx}:{handler_idx}"
        return keys, hooks_json_path

    def test_reports_trusted_and_untrusted_split(self):
        install_hooks.install(self.home, ROOT, ["wiki-loader", "no-speculation"], ["codex"])
        keys, hooks_json_path = self._hook_keys()

        config_toml = self.home / ".codex" / "config.toml"
        trusted_key = f"{hooks_json_path}:{keys['wiki-loader']}"
        config_toml.write_text(
            f'[hooks.state."{trusted_key}"]\n'
            'trusted_hash = "deadbeef"\n',
            encoding="utf-8",
        )

        status = install_hooks.codex_trust_status(self.home, ROOT)
        self.assertEqual(status["total_count"], 2)
        self.assertEqual(status["trusted_count"], 1)
        self.assertEqual(status["remaining"], 1)

        by_id = {h["id"]: h["trusted"] for h in status["hooks"]}
        self.assertTrue(by_id["wiki-loader"])
        self.assertFalse(by_id["no-speculation"])

    def test_no_config_toml_reports_all_untrusted(self):
        install_hooks.install(self.home, ROOT, ["wiki-loader"], ["codex"])
        status = install_hooks.codex_trust_status(self.home, ROOT)
        self.assertEqual(status["total_count"], 1)
        self.assertEqual(status["trusted_count"], 0)
        self.assertFalse(status["hooks"][0]["trusted"])


class GrokTargetTest(unittest.TestCase):
    """Task 6.18 step 3: grok-only writes ~/.grok/hooks/keel.json; when
    Claude Code is also selected, no separate Grok file is written."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()

    def test_grok_only_writes_keel_json(self):
        result = install_hooks.install(self.home, ROOT, ["wiki-loader", "no-speculation"], ["grok"])
        self.assertEqual(set(result["grok"]), {"wiki-loader", "no-speculation"})
        self.assertFalse(result["grok_via_claude"])

        grok_path = self.home / ".grok" / "hooks" / "keel.json"
        self.assertTrue(grok_path.exists())
        grok_json = json.loads(grok_path.read_text(encoding="utf-8"))
        session_start_cmds = [
            h["command"] for grp in grok_json["hooks"]["SessionStart"] for h in grp["hooks"]
        ]
        self.assertTrue(any(c.endswith('wiki_loader.py"') for c in session_start_cmds))

        ids = install_hooks.installed_ids(self.home)
        self.assertEqual(set(ids["grok"]), {"wiki-loader", "no-speculation"})

    def test_claude_and_grok_writes_no_grok_file(self):
        result = install_hooks.install(self.home, ROOT, ["wiki-loader"], ["claude", "grok"])
        self.assertTrue(result["grok_via_claude"])
        self.assertEqual(set(result["grok"]), {"wiki-loader"})
        grok_path = self.home / ".grok" / "hooks" / "keel.json"
        self.assertFalse(grok_path.exists())

        settings = json.loads((self.home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertIn("SessionStart", settings["hooks"])

    def test_stale_grok_file_removed_when_claude_added_later(self):
        install_hooks.install(self.home, ROOT, ["wiki-loader"], ["grok"])
        grok_path = self.home / ".grok" / "hooks" / "keel.json"
        self.assertTrue(grok_path.exists())

        install_hooks.install(self.home, ROOT, ["wiki-loader"], ["claude", "grok"])
        self.assertFalse(grok_path.exists())

    def test_grok_only_uninstall_removes_the_file(self):
        install_hooks.install(self.home, ROOT, ["wiki-loader"], ["grok"])
        grok_path = self.home / ".grok" / "hooks" / "keel.json"
        self.assertTrue(grok_path.exists())

        result = install_hooks.uninstall(self.home, ["grok"])
        self.assertEqual(result["grok"], 1)
        self.assertFalse(grok_path.exists())

    def test_uninstall_without_grok_target_leaves_file_in_place(self):
        install_hooks.install(self.home, ROOT, ["wiki-loader"], ["grok"])
        grok_path = self.home / ".grok" / "hooks" / "keel.json"
        install_hooks.uninstall(self.home, ["claude"])
        self.assertTrue(grok_path.exists())


if __name__ == "__main__":
    unittest.main()
