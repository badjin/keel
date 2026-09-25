import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_p7_auto_update import AutoUpdateTestBase


HOOK = Path(__file__).resolve().parents[1] / "kit" / "hooks" / "_wiki_guard.py"
BASE = b"# Page\nfirst fact\nsecond fact\n## Last Updated: 2026-09-01 created by hand\n"


def load_guard():
    spec = importlib.util.spec_from_file_location("_wiki_guard_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CheckUpdateTests(unittest.TestCase):
    def setUp(self):
        self.guard = load_guard()
        self.before = {"wiki/page.md": BASE}

    def test_insert_line_and_create_page(self):
        after = {
            "wiki/page.md": BASE.replace(b"first fact\n", b"first fact\nnew fact\n"),
            "wiki/new.md": b"# New\n",
        }
        self.assertEqual(self.guard.check_update(self.before, after), [])

    def test_remove_fact(self):
        after = {"wiki/page.md": BASE.replace(b"second fact\n", b"")}
        self.assertEqual(self.guard.check_update(self.before, after), [("changed", "wiki/page.md")])

    def test_reword_fact(self):
        after = {"wiki/page.md": BASE.replace(b"first fact", b"first fact!")}
        self.assertEqual(self.guard.check_update(self.before, after), [("changed", "wiki/page.md")])

    def test_swap_facts(self):
        after = {"wiki/page.md": BASE.replace(b"first fact\nsecond fact", b"second fact\nfirst fact")}
        self.assertEqual(self.guard.check_update(self.before, after), [("changed", "wiki/page.md")])

    def test_footer_date_change_keeps_note(self):
        after = {"wiki/page.md": BASE.replace(b"2026-09-01", b"2026-09-25")}
        self.assertEqual(self.guard.check_update(self.before, after), [])

    def test_footer_note_removed(self):
        after = {"wiki/page.md": BASE.replace(b"2026-09-01 created by hand", b"2026-09-25")}
        self.assertEqual(self.guard.check_update(self.before, after), [("changed", "wiki/page.md")])

    def test_footer_note_must_survive_after_candidate_date(self):
        cases = (
            (b"Updated", b"", [("changed", "wiki/page.md")]),
            (b"2026", b"", [("changed", "wiki/page.md")]),
            (b"Updated", b" Updated", []),
        )
        for old_note, new_note, expected in cases:
            with self.subTest(old_note=old_note, new_note=new_note):
                before = {"wiki/page.md": b"## Last Updated: 2026-09-01 " + old_note + b"\n"}
                after = {"wiki/page.md": b"## Last Updated: 2026-09-25" + new_note + b"\n"}
                self.assertEqual(self.guard.check_update(before, after), expected)

    def test_second_footer(self):
        after = {"wiki/page.md": BASE + b"## Last Updated: 2026-09-25\n"}
        self.assertEqual(self.guard.check_update(self.before, after), [("footer", "wiki/page.md")])

    def test_outside_paths(self):
        for rel in ("raw/x.md", "wiki/a.txt"):
            with self.subTest(rel=rel):
                after = dict(self.before, **{rel: b"new\n"})
                self.assertEqual(self.guard.check_update(self.before, after), [("outside", rel)])

    def test_deleted_page(self):
        self.assertEqual(self.guard.check_update(self.before, {}), [("deleted", "wiki/page.md")])

    def test_index_topic_added(self):
        before = {"index.md": b"# Index\n## Topics\n"}
        after = {"index.md": b"# Index\n## Topics\n- New topic\n"}
        self.assertEqual(self.guard.check_update(before, after), [])


class ApplyChangesTests(unittest.TestCase):
    def setUp(self):
        self.guard = load_guard()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wiki = self.root / "live"
        (self.wiki / "wiki").mkdir(parents=True)
        (self.wiki / "wiki" / "page.md").write_bytes(BASE)
        self.before = {"wiki/page.md": BASE}
        self.after = {
            "wiki/page.md": BASE.replace(b"first fact\n", b"first fact\nnew fact\n"),
            "wiki/sub/new.md": b"# New\n",
        }
        self.changed = self.guard.changed_paths(self.before, self.after)

    def test_apply_existing_and_nested_new_page(self):
        self.assertTrue(self.guard.apply_changes(self.wiki, self.before, self.after, self.changed))
        self.assertEqual((self.wiki / "wiki" / "page.md").read_bytes(), self.after["wiki/page.md"])
        self.assertEqual((self.wiki / "wiki" / "sub" / "new.md").read_bytes(), self.after["wiki/sub/new.md"])

    def test_live_conflict_prevents_all_writes(self):
        user_bytes = b"user edit\n"
        (self.wiki / "wiki" / "page.md").write_bytes(user_bytes)
        self.assertFalse(self.guard.apply_changes(self.wiki, self.before, self.after, self.changed))
        self.assertEqual((self.wiki / "wiki" / "page.md").read_bytes(), user_bytes)
        self.assertFalse((self.wiki / "wiki" / "sub" / "new.md").exists())

    def test_symlink_component_prevents_target_write(self):
        target = self.root / "target"
        target.mkdir()
        (target / "page.md").write_bytes(BASE)
        (self.wiki / "wiki" / "page.md").unlink()
        (self.wiki / "wiki").rmdir()
        (self.wiki / "wiki").symlink_to(target, target_is_directory=True)
        self.assertFalse(self.guard.apply_changes(self.wiki, self.before, self.after, self.changed))
        self.assertEqual((target / "page.md").read_bytes(), BASE)
        self.assertFalse((target / "sub" / "new.md").exists())

    def test_notice_count_and_take_once(self):
        self.guard.record_notice(self.root, "rejected", path="wiki/first.md")
        self.guard.record_notice(self.root, "conflict", path="wiki/second.md")
        notice = self.guard.take_notice(self.root)
        self.assertEqual(notice["count"], 2)
        self.assertEqual(notice["path"], "wiki/second.md")
        self.assertIsNone(self.guard.take_notice(self.root))


class WorkerGuardTests(AutoUpdateTestBase):
    def setUp(self):
        super().setUp()
        page = self.wiki / "wiki" / "page.md"
        page.parent.mkdir()
        page.write_text("# Page\nfirst fact\nsecond fact\n", encoding="utf-8")

    def write_job(self, session_id):
        state_dir = self.kit_home / "state" / "auto-update"
        state_dir.mkdir(parents=True, exist_ok=True)
        job_path = state_dir / f"{session_id}.json"
        job_path.write_text(json.dumps({
            "session_id": session_id,
            "cwd": str(self.tmp.name),
            "git_root": None,
            "cli": "claude",
            "wiki_path": str(self.wiki),
            "user_requests": "some request",
            "user_message_count": 1,
            "handled_before": 0,
            "last_assistant": "some answer",
        }), encoding="utf-8")
        return job_path

    def run_worker_subprocess(self, job_path, env):
        return subprocess.run(
            [sys.executable, str(job_path.parents[2] / "hooks" / "_auto_update_worker.py")],
            capture_output=True, text=True, env=env, timeout=20,
        )

    def test_drop_rejected_preserves_live_files_and_advances_ledger(self):
        before = {p.relative_to(self.wiki): p.read_bytes() for p in self.wiki.rglob("*") if p.is_file()}
        job_path = self.write_job("drop1")
        proc = self.run_worker_subprocess(job_path, self.env_with_path({"FAKE_CLI_EDIT": "drop"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("rejected: changed wiki/page.md", self.log_text())
        after = {p.relative_to(self.wiki): p.read_bytes() for p in self.wiki.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        notice = json.loads((self.kit_home / "state" / "auto-update-notice.json").read_text(encoding="utf-8"))
        self.assertEqual(notice["kind"], "rejected")
        ledger = json.loads((self.kit_home / "state" / "auto-update-ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["drop1"]["count"], 1)

    def test_live_edit_conflicts_without_overwriting_or_advancing_ledger(self):
        page = self.wiki / "wiki" / "page.md"
        job_path = self.write_job("conflict1")
        env = self.env_with_path({"FAKE_CLI_EDIT": "append", "FAKE_CLI_TOUCH_LIVE": str(page)})
        proc = self.run_worker_subprocess(job_path, env)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("session=conflict1 conflict", self.log_text())
        self.assertTrue(page.read_text(encoding="utf-8").endswith("user edit during run\n"))
        self.assertNotIn("- 2026-09-25 correction: new fact", page.read_text(encoding="utf-8"))
        notice = json.loads((self.kit_home / "state" / "auto-update-notice.json").read_text(encoding="utf-8"))
        self.assertEqual(notice["kind"], "conflict")
        ledger_path = self.kit_home / "state" / "auto-update-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
        self.assertNotIn("conflict1", ledger)

    def test_symlinked_live_wiki_folder_conflicts_without_target_write(self):
        target = Path(self.tmp.name) / "target"
        target.mkdir()
        page = self.wiki / "wiki" / "page.md"
        page.unlink()
        page.parent.rmdir()
        (self.wiki / "wiki").symlink_to(target, target_is_directory=True)
        job_path = self.write_job("symlink1")
        proc = self.run_worker_subprocess(job_path, self.env_with_path({"FAKE_CLI_EDIT": "create"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("conflict", self.log_text())
        self.assertFalse((target / "new.md").exists())

    def test_missing_wiki_folder_is_not_created_or_recorded(self):
        session_id = "missing1"
        missing_wiki = Path(self.tmp.name) / "moved-kb"
        job_path = self.write_job(session_id)
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["wiki_path"] = str(missing_wiki)
        job_path.write_text(json.dumps(job), encoding="utf-8")

        proc = self.run_worker_subprocess(job_path, self.env_with_path({"FAKE_CLI_EDIT": "create"}))

        self.assertEqual(proc.returncode, 0)
        self.assertFalse(missing_wiki.exists())
        self.assertIn("session=missing1 error: wiki_path missing", self.log_text())
        ledger_path = self.kit_home / "state" / "auto-update-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
        self.assertNotIn(session_id, ledger)


if __name__ == "__main__":
    unittest.main()
