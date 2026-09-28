from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from kit.workflow import approval, critic, docs, fsutil, work


SOURCE_KIT = Path(__file__).resolve().parents[1] / "kit"


def answer(stage, failed=None):
    return "".join(f"CHECK {name}: {'FAIL' if name == failed else 'PASS'} — ok\n"
                   for name in critic.EXPECTED_CHECKS[stage]) + "VERDICT: PASS\n"


class CriticTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.kb = self.root / "kb"
        self.folder = work.new_work(self.kb, SOURCE_KIT, "example", "en", "cli", "2026-09-28")
        intent = self.folder / "intent.md"
        intent.write_text(intent.read_text()
                          .replace("## Summary\n", "## Summary\nFix the workflow.\n")
                          .replace("## Problem\n", "## Problem\nThe workflow needs a fix.\n")
                          .replace("## Proposed outcome\n", "## Proposed outcome\n- Fix the workflow. [Q1]\n")
                          .replace("## Knowledge base consulted\n",
                                   "## Knowledge base consulted\n- `index.md` — Workflow overview.\n")
                          .replace("## Constraints\n", "## Constraints\n- Keep the workflow scoped. [Q1]\n")
                          .replace("## Open questions\n", "## Open questions\nNone.\n")
                          .replace("## Quotes\n", "## Quotes\n**Q1** Please fix the workflow.\n"))
        self.out = self.root / "fake.json"
        bindir = self.root / "bin"
        bindir.mkdir()
        fake = bindir / "claude"
        fake.write_text("#!/usr/bin/env python3\nimport json, os, pathlib, sys, time\n"
                        "time.sleep(float(os.environ.get('FAKE_SLEEP', '0')))\n"
                        "pathlib.Path(os.environ['FAKE_OUT']).write_text(json.dumps({"
                        "'argv': sys.argv[1:], 'cwd': os.getcwd(), "
                        "'cwd_files': os.listdir('.'), "
                        "'env_nested': os.environ.get('LLM_WIKI_KIT_NESTED'), "
                        "'stdin': sys.stdin.read()}))\n"
                        "print(os.environ.get('FAKE_ANSWER', ''))\n", encoding="utf-8")
        fake.chmod(0o755)
        fsutil.atomic_write_json(self.home / "config.json", {"workflow": {"critic_cli": "claude", "language": "en"}})
        self.env = patch.dict(os.environ, {
            "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_OUT": str(self.out), "FAKE_ANSWER": answer("intent"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.statements = self.root / "statements.txt"
        self.statements.write_text("Please fix the workflow.\n", encoding="utf-8")
        (self.kb / "index.md").write_text("Workflow overview.\n", encoding="utf-8")
        self.transcript = self.root / "session.jsonl"
        self.transcript.write_text("\n".join(json.dumps(row) for row in [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": str(self.kb / "index.md")}}]}},
            {"type": "user", "message": {"role": "user", "content": "Please fix the workflow."}},
        ]), encoding="utf-8")
        work.add_session(self.folder, "default", "claude", str(self.transcript))

    def review(self, stage="intent", **kwargs):
        if stage == "intent" and "statements" not in kwargs:
            kwargs["statements"] = self.statements
        with patch.dict(os.environ, {"FAKE_ANSWER": os.environ.get("FAKE_ANSWER", answer("intent"))
                         if stage == "intent" else answer(stage)}):
            return critic.run_review(stage, keel_home=self.home, kb=self.kb, folder=self.folder, **kwargs)

    def approve(self):
        self.review()
        path = self.folder / "intent.md"
        fsutil.atomic_write_text(path, docs.set_status(path.read_text(encoding="utf-8"), "awaiting-approval"))
        approval.approve(self.kb, self.home, self.folder.name, "s1", f"approve {self.folder.name}")

    def test_pass_is_isolated_and_recorded(self):
        verdict, record, checks, result = self.review()
        self.assertEqual(verdict, "PASS")
        self.assertEqual(len(checks), len(critic.EXPECTED_CHECKS["intent"]) + 1)
        self.assertIn("VERDICT: PASS", result)
        self.assertTrue(record.is_file())
        self.assertEqual(work.load_state(self.folder)["reviews"]["intent"]["verdict"], "PASS")
        seen = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(seen["env_nested"], "1")
        self.assertNotEqual(seen["cwd"], str(self.folder))
        self.assertEqual(seen["cwd_files"], [])
        self.assertTrue(seen["stdin"].startswith("KEEL-CRITIC"))
        self.assertEqual(seen["argv"][seen["argv"].index("--setting-sources") + 1], "project,local")
        self.assertEqual(seen["argv"][seen["argv"].index("--disallowedTools") + 1:],
                         ["Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"])

    def test_missing_verdict_is_error(self):
        with patch.dict(os.environ, {"FAKE_ANSWER": "CHECK x: PASS — ok"}):
            verdict, _, _, _ = self.review()
        self.assertEqual(verdict, "ERROR")
        self.assertNotEqual(work.load_state(self.folder)["reviews"]["intent"]["verdict"], "PASS")

    def test_fail_check_overrides_pass(self):
        with patch.dict(os.environ, {"FAKE_ANSWER": answer("intent", "outcome-traced")}):
            verdict, _, _, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(work.load_state(self.folder)["review_fails"]["intent"], 1)

    def test_timeout_is_error(self):
        with patch.dict(os.environ, {"FAKE_SLEEP": "2", "KEEL_WF_CRITIC_TIMEOUT": "1"}):
            verdict, _, _, _ = self.review()
        self.assertEqual(verdict, "ERROR")

    def test_spec_refuses_before_approval(self):
        with self.assertRaises(critic.Refused):
            self.review("spec")
        self.assertFalse(self.out.exists())

    def test_plan_refuses_changed_spec(self):
        self.approve()
        spec = self.folder / "spec.md"
        spec.write_text("# Spec\n- Intent: intent.md\n## Acceptance Scenarios\n- S1 See result.\n", encoding="utf-8")
        self.review("spec")
        spec.write_text(spec.read_text(encoding="utf-8").replace("See result", "See other result"), encoding="utf-8")
        with self.assertRaises(critic.Refused):
            self.review("plan")

    def test_plan_with_branch_step_fails_without_cli(self):
        self.approve()
        spec = self.folder / "spec.md"
        spec.write_text("# Spec\n- Intent: intent.md\n## Acceptance Scenarios\n- S1 See result.\n", encoding="utf-8")
        self.review("spec")
        (self.folder / "plan.md").write_text(
            "---\nkeel_plan: 1\n---\n## Phase 1: Work\n- [x] Run `git switch -c feature/x`\n", encoding="utf-8")
        self.out.unlink()
        verdict, record, checks, _ = self.review("plan")
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(checks[0].startswith("CHECK no-outward-steps: FAIL"))
        self.assertIn("git switch -c feature/x", checks[0])
        self.assertFalse(self.out.exists())
        self.assertNotIn("## Answer", record.read_text(encoding="utf-8"))

    def test_spec_scenarios_and_plan_gate(self):
        self.approve()
        spec = self.folder / "spec.md"
        spec.write_text("# Spec\n- Intent: intent.md\n## Acceptance Scenarios\n- **S1** Open page.\n- S2 Save page.\n", encoding="utf-8")
        (self.folder / "plan.md").write_text("# Plan\n", encoding="utf-8")
        verdict, record, _, _ = self.review("spec")
        self.assertEqual(verdict, "PASS")
        body = record.read_text(encoding="utf-8")
        self.assertIn("## Approved scenarios\n- **S1** Open page.\n- S2 Save page.", body)
        self.assertIn(f"- Scenario review: records/{record.name}", spec.read_text(encoding="utf-8"))
        self.assertEqual(self.review("plan")[0], "PASS")

    def test_spec_change_during_review_preserves_new_text(self):
        self.approve()
        spec = self.folder / "spec.md"
        spec.write_text("# Spec\n- Intent: intent.md\n## Acceptance Scenarios\n- S1 Original.\n", encoding="utf-8")

        def change_during_review(*args, **kwargs):
            result = subprocess.run(*args, **kwargs)
            spec.write_text(spec.read_text(encoding="utf-8").replace("Original.", "Changed."), encoding="utf-8")
            return result

        with self.assertRaisesRegex(critic.ReviewError, "spec changed during the review"):
            self.review("spec", run=change_during_review)
        self.assertIn("- S1 Changed.", spec.read_text(encoding="utf-8"))
        self.assertNotIn("- Scenario review:", spec.read_text(encoding="utf-8"))

    def test_transcripts_and_statements(self):
        claude = self.root / "claude.jsonl"
        codex = self.root / "codex.jsonl"
        claude.write_text('\n'.join(json.dumps(row) for row in [
            {"type": "user", "message": {"role": "user", "content": "First user."}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Assistant text."}]}},
            {"type": "user", "message": {"role": "user", "content": "1"}},
            {"type": "user", "message": {"role": "user", "content": "<environment_context>synthetic"}},
        ]), encoding="utf-8")
        codex.write_text('\n'.join(json.dumps(row) for row in [
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Second user."}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Other assistant."}]}},
        ]), encoding="utf-8")
        work.add_session(self.folder, "s1", "claude", str(claude))
        work.add_session(self.folder, "s2", "codex", str(codex))
        self.review(statements=None)
        prompt = json.loads(self.out.read_text(encoding="utf-8"))["stdin"]
        self.assertLess(prompt.index("First user."), prompt.index("Second user."))
        self.assertIn("Agent asked: Assistant text.", prompt)
        self.assertNotIn("Other assistant.", prompt)
        self.assertNotIn("synthetic", prompt)
        self.review(statements=self.statements)
        prompt = json.loads(self.out.read_text(encoding="utf-8"))["stdin"]
        self.assertIn("Please fix the workflow.", prompt)
        self.assertNotIn("First user.", prompt)

    def test_no_transcript_refuses(self):
        self.transcript.unlink()
        with self.assertRaises(critic.Refused):
            self.review(statements=None)

    def test_statements_cannot_bypass_missing_transcript(self):
        self.transcript.unlink()
        with self.assertRaisesRegex(critic.Refused, "there is no bypass"):
            self.review(statements=self.statements)
        self.assertFalse(self.out.exists())
        self.assertEqual(work.load_state(self.folder)["review_fails"], {})
        self.assertEqual(list((self.folder / "records").iterdir()), [])

    def test_empty_kb_list_fails_without_cli_or_answer(self):
        intent = self.folder / "intent.md"
        intent.write_text(intent.read_text().replace("- `index.md`", "No pages listed."))
        verdict, record, checks, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("CHECK kb-consulted: FAIL", checks[0])
        self.assertNotIn("## Answer", record.read_text())
        self.assertFalse(self.out.exists())
        self.assertEqual(work.load_state(self.folder)["review_fails"]["intent"], 1)

    def test_unread_page_fails(self):
        self.transcript.write_text(json.dumps({"type": "user", "message": {"content": "Request"}}))
        verdict, record, _, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("index.md", record.read_text())
        self.assertFalse(self.out.exists())

    def test_claude_write_does_not_prove_kb_read(self):
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {
                "file_path": str(self.folder / "intent.md"),
                "content": f"- `{self.kb / 'index.md'}` — Workflow overview."}}]}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("CHECK kb-consulted: FAIL", checks[0])
        self.assertIn("unread knowledge base page", checks[0])

    def test_claude_heredoc_to_work_folder_does_not_prove_kb_read(self):
        command = (f"cat > '{self.folder / 'intent.md'}' <<'EOF'\n"
                   f"- `{self.kb / 'index.md'}` — Workflow overview.\nEOF")
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("CHECK kb-consulted: FAIL", checks[0])
        self.assertIn("unread knowledge base page", checks[0])

    def test_shell_variable_kb_reads(self):
        wiki_page = self.kb / "wiki" / "a.md"
        wiki_page.parent.mkdir()
        wiki_page.write_text("Page")
        intent = self.folder / "intent.md"
        original = intent.read_text()
        for command, page in ((f"KB={self.kb}; cat $KB/index.md", "index.md"),
                              (f'export KB="{self.kb}"; cat "${{KB}}/wiki/a.md"', "wiki/a.md")):
            with self.subTest(command=command):
                intent.write_text(original.replace("`index.md`", f"`{page}`"))
                self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}))
                verdict, _, _, _ = self.review()
                self.assertEqual(verdict, "PASS")

    def test_home_variable_kb_read(self):
        command = 'KB="$HOME/kb"; cat "$KB/index.md"'
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}))
        verdict, _, _, _ = self.review()
        self.assertEqual(verdict, "PASS")

    def test_unassigned_shell_variable_does_not_prove_kb_read(self):
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "cat $KB/index.md"}}]}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("unread knowledge base page", checks[0])

    def test_shell_variable_work_folder_heredoc_does_not_prove_kb_read(self):
        command = (f"W={self.folder}; cat > $W/intent.md <<EOF\n"
                   f"- `{self.kb / 'index.md'}` — Workflow overview.\nEOF")
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("unread knowledge base page", checks[0])
        self.assertFalse(self.out.exists())

    def test_home_variable_work_folder_heredoc_does_not_prove_kb_read(self):
        command = (f'cat > "$HOME/kb/raw/work/{self.folder.name}/intent.md" <<EOF\n'
                   f"- `{self.kb / 'index.md'}` — Workflow overview.\nEOF")
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("unread knowledge base page", checks[0])
        self.assertFalse(self.out.exists())

    def test_kb_variable_heredoc_to_work_folder_does_not_prove_kb_read(self):
        command = (f"KB={self.kb}; cat > $KB/raw/work/{self.folder.name}/intent.md <<EOF\n"
                   f"- `{self.kb / 'index.md'}` — Workflow overview.\nEOF")
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("unread knowledge base page", checks[0])

    def test_nested_variable_heredoc_to_work_folder_does_not_prove_kb_read(self):
        command = (f"KB={self.kb}; W=$KB/raw/work/{self.folder.name}; cat > $W/intent.md <<EOF\n"
                   f"- `{self.kb / 'index.md'}` — Workflow overview.\nEOF")
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("unread knowledge base page", checks[0])

    def assert_write_is_not_a_read(self, command):
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("unread knowledge base page", checks[0])
        self.assertFalse(self.out.exists())

    def test_split_double_quoted_work_folder_write_does_not_prove_kb_read(self):
        self.assert_write_is_not_a_read(
            f'cat > "$HOME"/kb/raw/work/{self.folder.name}/intent.md <<EOF\n'
            f"- `{self.kb / 'index.md'}` — Workflow overview.\nEOF")

    def test_single_quoted_segment_work_folder_write_does_not_prove_kb_read(self):
        self.assert_write_is_not_a_read(
            f"cat > {self.root}/'kb'/raw/work/{self.folder.name}/intent.md <<EOF\n"
            f"- `{self.kb / 'index.md'}` — Workflow overview.\nEOF")

    def test_escaped_space_work_folder_write_does_not_prove_kb_read(self):
        spaced = self.root / "my kb"
        spaced.mkdir()
        (spaced / "index.md").write_text("Workflow overview.\n", encoding="utf-8")
        folder = work.new_work(spaced, SOURCE_KIT, "spaced", "en", "cli", "2026-09-28")
        (folder / "intent.md").write_text((self.folder / "intent.md").read_text(encoding="utf-8"), encoding="utf-8")
        work.add_session(folder, "default", "claude", str(self.transcript))
        self.kb, self.folder = spaced, folder
        escaped = str(folder).replace(" ", "\\ ")
        self.assert_write_is_not_a_read(
            f"cat > {escaped}/intent.md <<EOF\n- `{spaced / 'index.md'}` — Workflow overview.\nEOF")

    def test_quoted_read_still_proves_kb_read(self):
        self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": 'cat "$HOME"/kb/index.md'}}]}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "PASS")
        self.assertIn("CHECK kb-consulted: PASS", checks[-1])

    def test_kb_assignment_after_and_proves_read(self):
        for command in (f"cd /tmp && KB={self.kb} && cat $KB/index.md",
                        f"bash -lc KB={self.kb}; cat $KB/index.md"):
            with self.subTest(command=command):
                self.transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}))
                verdict, _, checks, _ = self.review()
                self.assertEqual(verdict, "PASS")
                self.assertIn("CHECK kb-consulted: PASS", checks[-1])

    def test_codex_apply_patch_does_not_prove_kb_read(self):
        patch_text = (f"*** Begin Patch\n*** Update File: {self.folder / 'intent.md'}\n"
                      f"+ - `{self.kb / 'index.md'}` — Workflow overview.\n*** End Patch")
        for payload in (
            {"type": "custom_tool_call", "name": "apply_patch", "input": patch_text},
            {"type": "function_call", "name": "apply_patch",
             "arguments": json.dumps({"patch": patch_text})},
        ):
            with self.subTest(call_type=payload["type"]):
                self.transcript.write_text(json.dumps({"type": "response_item", "payload": payload}))
                verdict, _, checks, _ = self.review()
                self.assertEqual(verdict, "FAIL")
                self.assertIn("CHECK kb-consulted: FAIL", checks[0])
                self.assertIn("unread knowledge base page", checks[0])

    def test_codex_non_json_function_call_proves_kb_read(self):
        self.transcript.write_text(json.dumps({"type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command",
            "arguments": f"cat '{self.kb / 'index.md'}'"}}))
        verdict, _, checks, _ = self.review()
        self.assertEqual(verdict, "PASS")
        self.assertIn("CHECK kb-consulted: PASS", checks[-1])

    def test_invalid_kb_pages_fail(self):
        outside = self.root / "outside.md"
        outside.write_text("Outside")
        inside = self.folder / "inside.md"
        inside.write_text("Inside")
        intent = self.folder / "intent.md"
        original = intent.read_text()
        for page in (str(outside), "missing.md", str(inside)):
            with self.subTest(page=page):
                intent.write_text(original.replace("index.md", page))
                verdict, record, _, _ = self.review()
                self.assertEqual(verdict, "FAIL")
                self.assertIn(page, record.read_text())
                self.assertFalse(self.out.exists())

    def test_read_pages_pass_and_question_context_is_in_prompt(self):
        (self.kb / "second.md").write_text("Second page.")
        intent = self.folder / "intent.md"
        intent.write_text(intent.read_text().replace(
            "- `index.md` — Workflow overview.",
            "- `index.md` — Workflow overview.\n- `second.md` — Second page."))
        self.transcript.write_text("\n".join(json.dumps(row) for row in [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": str(self.kb / "index.md")}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": str(self.kb / "second.md")}},
                {"type": "text", "text": "Which option?"}]}},
            {"type": "user", "message": {"content": "1"}},
        ]))
        verdict, record, _, _ = self.review(statements=None)
        self.assertEqual(verdict, "PASS")
        self.assertIn("CHECK kb-consulted: PASS — 2 pages read", record.read_text())
        prompt = json.loads(self.out.read_text())["stdin"]
        self.assertLess(prompt.index("Agent asked: Which option?"), prompt.index("User: 1"))

    def test_fourth_intent_review_refuses(self):
        with patch.dict(os.environ, {"FAKE_ANSWER": answer("intent", "outcome-traced")}):
            for _ in range(3):
                self.assertEqual(self.review()[0], "FAIL")
            self.out.unlink()
            with self.assertRaises(critic.Refused):
                self.review()
        self.assertFalse(self.out.exists())

    def test_bare_verdict_is_error_without_fail_count(self):
        with patch.dict(os.environ, {"FAKE_ANSWER": "VERDICT: PASS"}):
            self.assertEqual(self.review()[0], "ERROR")
        self.assertEqual(work.load_state(self.folder)["review_fails"], {})

    def test_environment_and_codex_command(self):
        env = critic.child_env({"CLAUDECODE": "x", "CLAUDE_CODE_TEST": "y", "KEEP": "z"})
        self.assertEqual(env, {"KEEP": "z", "LLM_WIKI_KIT_NESTED": "1"})
        cmd = critic.build_cmd("codex", "codex", "/tmp")
        self.assertEqual(cmd[cmd.index("-s") + 1], "read-only")
        self.assertIn("features.hooks=false", cmd)

    def test_plan_refuses_unapproved_intent(self):
        with self.assertRaises(critic.Refused):
            self.review("plan")

    def test_third_phase_review_requires_extra_review(self):
        state = work.load_state(self.folder)
        state["review_fails"]["phase-1"] = 2
        fsutil.atomic_write_json(self.folder / "state.json", state)
        with self.assertRaises(critic.Refused):
            self.review("phase", phase=1, repo=self.root)

    def test_transcript_filters_synthetic_and_includes_absorbed(self):
        from kit.workflow import transcripts
        path = self.root / "messages.jsonl"
        rows = [
            {"type": "user", "message": {"content": "Normal"}},
            {"type": "user", "message": {"content": "Base directory for this skill: /x"}},
            {"type": "user", "message": {"content": "<task-notification>done"}},
            {"type": "user", "message": {"content": "<hook_prompt synthetic"}},
            {"type": "user", "isCompactSummary": True,
             "message": {"content": "This session is being continued from a previous conversation"}},
            {"type": "user", "isMeta": True, "message": {"content": "Meta row"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<hook_prompt synthetic"}]}},
            {"type": "queue-operation", "operation": "remove", "reason": "absorbed_mid_turn", "content": "Absorbed"},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows))
        self.assertEqual(transcripts.user_messages(str(path)), ["Normal", "Absorbed"])

    def test_tool_call_read_evidence(self):
        from kit.workflow import transcripts
        page = self.home / "KB" / "한글 page.md"
        page.parent.mkdir(parents=True)
        page.write_text("Page", encoding="utf-8")
        claude = self.root / "claude-tools.jsonl"
        claude.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(page)}}]}}), encoding="utf-8")
        codex = self.root / "codex-tools.jsonl"
        codex.write_text(json.dumps({"type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command",
            "arguments": json.dumps({"cmd": f"cat '{page}'"})}}), encoding="utf-8")
        for transcript in (claude, codex):
            with self.subTest(transcript=transcript.name):
                self.assertTrue(transcripts.read_evidence(
                    transcripts.tool_call_texts(str(transcript)), str(page), str(self.home)))

        nfd = unicodedata.normalize("NFD", str(page))
        self.assertTrue(transcripts.read_evidence([f"cat {nfd}"], str(page), str(self.home)))
        self.assertTrue(transcripts.read_evidence([f"cat {page}"], nfd, str(self.home)))
        self.assertTrue(transcripts.read_evidence(
            [f"cat ~/KB/한글\\ page.md"], str(page), str(self.home)))
        self.assertFalse(transcripts.read_evidence(["cat /elsewhere/page.md"], str(page), str(self.home)))
        self.assertEqual(transcripts.tool_call_texts(str(self.root / "missing.jsonl")), [])

    def test_user_messages_with_question_context(self):
        from kit.workflow import transcripts
        claude = self.root / "claude-question.jsonl"
        claude.write_text("\n".join(json.dumps(row) for row in [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Which option?"}]}},
            {"type": "user", "message": {"role": "user", "content": "1"}},
            {"type": "queue-operation", "operation": "remove", "reason": "absorbed_mid_turn",
             "content": "Follow-up"},
            {"type": "user", "message": {"role": "user", "content": "<environment_context>synthetic"}},
        ]), encoding="utf-8")
        codex = self.root / "codex-question.jsonl"
        codex.write_text("\n".join(json.dumps(row) for row in [
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                                 "content": [{"type": "output_text", "text": "Choose one."}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                 "content": [{"type": "input_text", "text": "1"}]}},
        ]), encoding="utf-8")
        self.assertEqual(transcripts.user_messages_with_context(str(claude)),
                         [("1", "Which option?"), ("Follow-up", "")])
        self.assertEqual(transcripts.user_messages_with_context(str(codex)), [("1", "Choose one.")])

    def test_light_review_reads_unicode_named_file(self):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "base.md").write_text("Base", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "base"], check=True)
        (repo / "한글.md").write_text("Unicode file content", encoding="utf-8")
        with patch.dict(os.environ, {"FAKE_ANSWER": answer("light")}):
            verdict, record, checks, result = critic.run_review(
                "light", keel_home=self.home, kb=self.kb, folder=None, repo=repo,
                request="Review this change")
        self.assertEqual((verdict, record), ("PASS", None))
        self.assertEqual(len(checks), len(critic.EXPECTED_CHECKS["light"]))
        self.assertIn("VERDICT: PASS", result)
        prompt = json.loads(self.out.read_text(encoding="utf-8"))["stdin"]
        self.assertIn("--- 한글.md ---\nUnicode file content", prompt)

    def test_phase_review_reads_latin1_diff_and_unicode_name(self):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "base.md").write_text("Base", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "base.md"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "base"], check=True)
        base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        (repo / "한글.md").write_text("Unicode file content", encoding="utf-8")
        (repo / "legacy.txt").write_bytes(b"Latin-1: caf\xe9\n")
        subprocess.run(["git", "-C", str(repo), "add", "한글.md", "legacy.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "change"], check=True)
        (self.folder / "spec.md").write_text("# Spec\n## Design\nDo it.\n", encoding="utf-8")
        (self.folder / "plan.md").write_text("## Phase 1: Change\n", encoding="utf-8")
        state = work.load_state(self.folder)
        state["run"] = {"phases": {"1": {"base": base}}}
        fsutil.atomic_write_json(self.folder / "state.json", state)
        verdict, _, _, _ = self.review("phase", phase=1, repo=repo)
        self.assertEqual(verdict, "PASS")
        prompt = json.loads(self.out.read_text(encoding="utf-8"))["stdin"]
        self.assertIn("--- 한글.md ---\nUnicode file content", prompt)
        self.assertIn("Latin-1: caf", prompt)


if __name__ == "__main__":
    unittest.main()
