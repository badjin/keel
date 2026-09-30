from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kit import install_hooks
from kit.workflow import docs, fsutil, install, ladder, work
from kit.workflow.rules_block import BlockError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "hooks_014"


class WorkflowInstallTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.kb = self.root / "kb"
        self.kb.mkdir()
        keel = self.home / ".keel"
        keel.mkdir()
        (keel / "config.json").write_text(json.dumps({"wiki_path": str(self.kb)}), encoding="utf-8")

    def data(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_round_trip_preserves_user_rules_hooks_and_kb(self):
        claude = self.home / ".claude" / "CLAUDE.md"
        codex = self.home / ".codex" / "AGENTS.md"
        claude.parent.mkdir()
        codex.parent.mkdir()
        claude.write_bytes(b"mine\n")
        codex.write_bytes(b"theirs")
        settings = claude.parent / "settings.json"
        original_hooks = {"Stop": [{"hooks": [{"type": "command", "command": "echo user"}]}]}
        settings.write_text(json.dumps({"hooks": original_hooks}), encoding="utf-8")
        work = self.kb / "raw" / "work"
        work.mkdir(parents=True)
        (work / "keep").write_text("yes", encoding="utf-8")

        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        self.assertEqual(self.data(self.home / ".keel/config.json")["workflow"]["language"], "en")
        install.uninstall(self.home)
        self.assertEqual(claude.read_bytes(), b"mine\n")
        self.assertEqual(codex.read_bytes(), b"theirs")
        self.assertEqual(self.data(settings)["hooks"], original_hooks)
        self.assertFalse((self.home / ".keel" / "workflow").exists())
        self.assertFalse((self.home / ".keel" / "workflow-hooks").exists())
        self.assertEqual((work / "keep").read_text(encoding="utf-8"), "yes")

    def test_ladder_json_and_agents_rendered(self):
        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        self.assertEqual(self.data(self.home / ".keel/ladder.json"), ladder.DEFAULTS)
        agents = self.home / ".claude" / "agents"
        for number, step in enumerate(ladder.DEFAULTS["steps"], 1):
            text = (agents / f"keel-hotfix-{number}.md").read_text(encoding="utf-8")
            self.assertIn(f"name: keel-hotfix-{number}\n", text)
            self.assertIn(f"model: {step['claude']['model']}\n", text)
            self.assertIn(f"effort: {step['claude']['effort']}\n", text)
            self.assertIn("<!-- keel -->", text)
        self.assertEqual(len(list(agents.glob("*.md"))), 2)
        self.assertFalse((self.home / ".codex" / "agents").exists())

    def test_edited_ladder_survives_reinstall_and_resizes_agents(self):
        install.install(self.home, ROOT, ["claude"], "claude", sys.executable)
        path = self.home / ".keel" / "ladder.json"
        step = {"claude": {"model": "opus", "effort": "high"}, "codex": {"model": None, "effort": "high"}}
        for count in (3, 1):
            edited = {"phase_review_limit": 5, "steps": [step] * count}
            path.write_text(json.dumps(edited), encoding="utf-8")
            install.install(self.home, ROOT, ["claude"], "claude", sys.executable)
            self.assertEqual(self.data(path), edited)
            names = sorted(p.name for p in (self.home / ".claude" / "agents").glob("keel-hotfix-*.md"))
            self.assertEqual(names, [f"keel-hotfix-{n}.md" for n in range(1, count + 1)])

    def test_unmarked_agent_file_is_kept(self):
        agents = self.home / ".claude" / "agents"
        agents.mkdir(parents=True)
        mine = agents / "keel-hotfix-9.md"
        mine.write_text("mine", encoding="utf-8")
        first = agents / "keel-hotfix-1.md"
        first.write_text("mine too", encoding="utf-8")
        install.install(self.home, ROOT, ["claude"], "claude", sys.executable)
        self.assertEqual(mine.read_text(encoding="utf-8"), "mine")
        self.assertEqual(first.read_text(encoding="utf-8"), "mine too")

    def test_uninstall_removes_marked_agents_keeps_ladder_and_unmarked(self):
        install.install(self.home, ROOT, ["claude"], "claude", sys.executable)
        agents = self.home / ".claude" / "agents"
        mine = agents / "my-agent.md"
        mine.write_text("mine", encoding="utf-8")
        install.uninstall(self.home)
        self.assertEqual([p.name for p in agents.iterdir()], ["my-agent.md"])
        self.assertTrue((self.home / ".keel" / "ladder.json").is_file())
        mine.unlink()
        install.install(self.home, ROOT, ["claude"], "claude", sys.executable)
        install.uninstall(self.home)
        self.assertFalse(agents.exists())

    def test_created_rules_removed(self):
        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        install.uninstall(self.home)
        self.assertFalse((self.home / ".claude" / "CLAUDE.md").exists())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())

    def test_reinstall_then_uninstall_removes_workflow_skills(self):
        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        second = install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        self.assertFalse(any("/skills/keel-" in path for path in second["backups"]))
        install.uninstall(self.home)
        for target in (".claude", ".codex"):
            self.assertEqual(list((self.home / target / "skills").glob("keel-*")), [])

    def test_reinstall_with_one_target_removes_other_target(self):
        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        metadata = self.home / ".keel/state/workflow/installed.json"
        self.assertTrue(metadata.exists())
        self.assertFalse((self.home / ".keel/workflow/installed.json").exists())

        result = install.install(self.home, ROOT, ["claude"], "codex", sys.executable)
        codex = self.home / ".codex"
        self.assertIn(str((codex / "AGENTS.md").resolve()), result["removed"])
        self.assertNotIn("keel:workflow:start", (codex / "AGENTS.md").read_text(encoding="utf-8") if (codex / "AGENTS.md").exists() else "")
        for groups in self.data(codex / "hooks.json")["hooks"].values():
            for group in groups:
                self.assertTrue(all("/.keel/workflow-hooks/" not in hook["command"] for hook in group["hooks"]))
        self.assertEqual(list((codex / "skills").glob("keel-*")), [])

        install.uninstall(self.home)
        self.assertFalse(metadata.exists())
        for target, rules_name, hooks_name in ((".claude", "CLAUDE.md", "settings.json"),
                                               (".codex", "AGENTS.md", "hooks.json")):
            cli = self.home / target
            self.assertNotIn("keel:workflow:start", (cli / rules_name).read_text(encoding="utf-8") if (cli / rules_name).exists() else "")
            self.assertEqual(list((cli / "skills").glob("keel-*")), [])
            for groups in self.data(cli / hooks_name)["hooks"].values():
                for group in groups:
                    self.assertTrue(all("/.keel/workflow-hooks/" not in hook["command"] for hook in group["hooks"]))

    def test_malformed_codex_rules_do_not_stop_uninstall(self):
        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        codex_rules = self.home / ".codex/AGENTS.md"
        codex_rules.write_text(codex_rules.read_text(encoding="utf-8") +
                               "<!-- keel:workflow:start 0.2.0 -->\n", encoding="utf-8")
        original = codex_rules.read_text(encoding="utf-8")
        with self.assertRaisesRegex(BlockError, str(codex_rules.resolve())):
            install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)

        result = install.uninstall(self.home)
        self.assertEqual(codex_rules.read_text(encoding="utf-8"), original)
        self.assertIn(str(codex_rules.resolve()), result["skipped"])
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())
        for target, hooks_name in ((".claude", "settings.json"), (".codex", "hooks.json")):
            cli = self.home / target
            self.assertEqual(list((cli / "skills").glob("keel-*")), [])
            for groups in self.data(cli / hooks_name)["hooks"].values():
                for group in groups:
                    self.assertTrue(all("/.keel/workflow-hooks/" not in hook["command"] for hook in group["hooks"]))

    def test_hook_indexes_stable_across_installers(self):
        user_hook = self.home / ".codex" / "hooks.json"
        user_hook.parent.mkdir()
        user_hook.write_text(json.dumps({"hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "echo user"}]}
        ]}}), encoding="utf-8")
        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        ids = json.loads((ROOT / "kit" / "catalogue.json").read_text(encoding="utf-8"))
        defaults = [item["id"] for item in ids if item.get("default")]
        path = self.home / ".codex" / "hooks.json"
        def commands():
            return {event: [group["hooks"][0]["command"] for group in groups]
                    for event, groups in self.data(path)["hooks"].items()}
        install_hooks.install(self.home, ROOT, defaults, ["claude", "codex"], python=sys.executable)
        first = commands()
        install_hooks.install(self.home, ROOT, defaults, ["claude", "codex"], python=sys.executable)
        self.assertEqual(commands(), first)
        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        self.assertEqual(commands(), first)
        for entries in first.values():
            kinds = ["workflow" if "/workflow-hooks/" in cmd else "kit" if "/.keel/hooks/" in cmd else "user" for cmd in entries]
            self.assertEqual(kinds, sorted(kinds, key={"user": 0, "kit": 1, "workflow": 2}.get))

    def test_without_workflow_matches_014_fixture(self):
        fixture = self.data(FIXTURE / "input.json")
        for name, content in ((".claude/settings.json", fixture["settings_before"]),
                              (".codex/hooks.json", fixture["hooks_before"])):
            path = self.home / name
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(content), encoding="utf-8")
        install_hooks.install(self.home, ROOT, fixture["hook_ids"], fixture["targets"], python=fixture["python"])
        for actual, expected in ((self.home / ".claude/settings.json", FIXTURE / "settings.json"),
                                 (self.home / ".codex/hooks.json", FIXTURE / "hooks.json")):
            self.assertEqual(actual.read_text(encoding="utf-8").replace(str(self.home), "{HOME}"),
                             expected.read_text(encoding="utf-8"))

    def test_explicit_home_used_for_all_paths_and_ledger(self):
        other = self.root / "other"
        other.mkdir()
        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)
        for path in (self.home / ".claude/CLAUDE.md", self.home / ".codex/AGENTS.md",
                     *list((self.home / ".claude/skills").glob("keel-*/SKILL.md")),
                     *list((self.home / ".codex/skills").glob("keel-*/SKILL.md"))):
            content = path.read_text(encoding="utf-8")
            self.assertIn(str(self.home), content)
            self.assertNotIn(str(other), content)
        for path in (self.home / ".claude/settings.json", self.home / ".codex/hooks.json"):
            for groups in self.data(path)["hooks"].values():
                for group in groups:
                    for hook in group["hooks"]:
                        self.assertIn(str(self.home), hook["command"])
                        self.assertNotIn(str(other), hook["command"])
        env = os.environ.copy()
        env["HOME"] = str(other)
        folder = work.new_work(self.kb, self.home / ".keel", "example", "en", str(self.root), "2026-09-28")
        intent = folder / "intent.md"
        content = (intent.read_text(encoding="utf-8")
                   .replace("## Summary\n", "## Summary\nUse the explicit home.\n")
                   .replace("## Problem\n", "## Problem\nHome paths need verification.\n")
                   .replace("## Proposed outcome\n", "## Proposed outcome\n- Use the explicit home. [Q1]\n")
                   .replace("## Knowledge base consulted\n",
                            "## Knowledge base consulted\n- `index.md` — Nothing on this yet.\n")
                   .replace("## Constraints\n", "## Constraints\n- Keep paths under the explicit home. [Q1]\n")
                   .replace("## Open questions\n", "## Open questions\nNone.\n")
                   .replace("## Quotes\n", "## Quotes\n**Q1** Use the explicit home.\n"))
        content = docs.set_status(content, "awaiting-approval")
        intent.write_text(content, encoding="utf-8")
        state = work.load_state(folder)
        state["reviews"]["intent"] = {"verdict": "PASS", "hashes": {"intent": docs.intent_hash(content)}}
        fsutil.atomic_write_json(folder / "state.json", state)
        result = subprocess.run([sys.executable, str(self.home / ".keel/workflow-hooks/wf_prompt.py"), "codex"],
                                input=json.dumps({"prompt": "approve example", "session_id": "s"}),
                                text=True, capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        ledger = self.home / ".keel/state/workflow/approvals.jsonl"
        self.assertEqual(json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])["session"], "s")
        self.assertFalse((other / ".keel/state/workflow").exists())

    def test_codex_trust_counts_workflow(self):
        install_hooks.install(self.home, ROOT, ["wiki-loader"], ["codex"])
        install.install(self.home, ROOT, ["codex"], "codex", sys.executable)
        status = install_hooks.codex_trust_status(self.home, ROOT)
        self.assertEqual({item["id"] for item in status["hooks"]},
                         {"wiki-loader", "wf:hook:prompt", "wf:hook:track"})
        self.assertEqual(status["total_count"], 3)

    def test_main_branch_guard_exempts_kb_only(self):
        kb_repo = self.kb / "repo"
        other_repo = self.root / "repo"
        for repo in (kb_repo, other_repo):
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        install_hooks.install(self.home, ROOT, ["main-branch-guard"], ["claude"])
        script = self.home / ".keel/hooks/main_branch_guard.py"
        for path, expected in ((kb_repo / "page.md", 0), (other_repo / "page.md", 2)):
            result = subprocess.run([sys.executable, str(script)], input=json.dumps({"tool_name": "Write",
                                    "tool_input": {"file_path": str(path)}, "cwd": str(path.parent)}),
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
