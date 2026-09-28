from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kit.workflow import check, install


ROOT = Path(__file__).resolve().parents[1]


class WorkflowCheckTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = Path(temporary.name) / "home"
        keel_home = self.home / ".keel"
        keel_home.mkdir(parents=True)
        kb = Path(temporary.name) / "kb"
        kb.mkdir()
        (keel_home / "config.json").write_text(json.dumps({"wiki_path": str(kb)}), encoding="utf-8")
        install.install(self.home, ROOT, ["claude", "codex"], "codex", sys.executable)

    def test_installed_workflow_rows(self):
        rows, exit_code = check.run_check(self.home / ".keel")
        self.assertEqual(exit_code, 0)
        self.assertTrue([row for row in rows if row["id"].startswith("wf:")])
        self.assertTrue(all(row["status"] == "installed" for row in rows
                            if row["id"].startswith("wf:")))

    def test_uninstalled_workflow_rows_are_missing(self):
        home = self.root / "uninstalled"
        rows, exit_code = check.run_check(home / ".keel")
        workflow_rows = [row for row in rows if row["id"].startswith("wf:")]
        self.assertTrue(workflow_rows)
        self.assertTrue(all(row["status"] == "missing" for row in workflow_rows))
        self.assertEqual(exit_code, 1)

    def test_manifest_item_only_checks_its_targets(self):
        manifest_path = self.home / ".keel/workflow/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = next(item for item in manifest["items"] if item["id"] == "wf:skill:keel-intent")
        item["targets"] = ["claude"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        rows, _ = check.run_check(self.home / ".keel")
        matching = [row for row in rows if row["id"] == item["id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(Path(matching[0]["where"]), (self.home / ".claude/skills/keel-intent/SKILL.md").resolve())

    def test_installed_cli_runs_outside_repo(self):
        cwd = self.root / "outside"
        cwd.mkdir()
        command = self.home / ".keel/workflow/keel_wf.py"
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"]}
        for args in (("check",), ("new", "demo")):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(command), *args], cwd=cwd,
                                        env=env, capture_output=True, text=True)
                self.assertNotIn("ModuleNotFoundError", result.stdout + result.stderr)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_includes_all_map_catalogue_hooks(self):
        workflow = self.home / ".keel/workflow"
        map_data = json.loads((workflow / "map.json").read_text(encoding="utf-8"))
        catalogue = json.loads((workflow / "catalogue.json").read_text(encoding="utf-8"))
        referenced = set()
        for stage in map_data["stages"] + map_data["side"]:
            referenced.update(stage.get("install", []))
            for node in stage.get("nodes", []):
                referenced.update(node.get("install", []))
        expected = referenced & {item["id"] for item in catalogue}
        rows, _ = check.run_check(self.home / ".keel")
        self.assertEqual(expected, {row["id"] for row in rows if row["id"] in expected})

    def test_missing_skill(self):
        (self.home / ".claude/skills/keel-intent/SKILL.md").unlink()
        rows, exit_code = check.run_check(self.home / ".keel")
        self.assertEqual(exit_code, 1)
        self.assertTrue(any(row["id"] == "wf:skill:keel-intent" and
                            row["status"] == "missing" for row in rows))

    def test_changed_rules_block(self):
        path = self.home / ".claude/CLAUDE.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("<!-- keel:workflow:end -->", "changed\n<!-- keel:workflow:end -->"),
                        encoding="utf-8")
        rows, exit_code = check.run_check(self.home / ".keel")
        self.assertEqual(exit_code, 1)
        self.assertTrue(any(row["id"] == "wf:rules" and row["status"] == "different"
                            for row in rows))

    def test_check_does_not_write(self):
        def snapshot():
            return {str(path): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
                    for path in self.home.rglob("*") if path.is_file()}

        before = snapshot()
        check.run_check(self.home / ".keel")
        self.assertEqual(snapshot(), before)


if __name__ == "__main__":
    unittest.main()
