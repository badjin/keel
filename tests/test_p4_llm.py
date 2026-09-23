from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from kit.history import Change
import kit.llm_pass as llm_pass_mod
from kit.llm_pass import available_clis, build_prompts, run_cli, summarize


def _change(i, area, date="2024-06-01T00:00:00+00:00"):
    return Change(
        sha=f"s{i}",
        is_merge=False,
        author="Alice",
        date=date,
        subject=f"change {i}",
        title=f"change {i}",
        pr=None,
        areas=[area],
    )


class BuildPromptsTest(unittest.TestCase):
    def test_shapes_match(self):
        changes = [_change(1, "src"), _change(2, "docs")]
        prompts = build_prompts("acme/widget", changes, "ko")
        self.assertIn("index", prompts)
        self.assertIn("months", prompts)
        self.assertIn("areas", prompts)
        self.assertIsInstance(prompts["index"], str)
        self.assertIsInstance(prompts["months"], dict)
        self.assertIsInstance(prompts["areas"], dict)

    def test_top_10_area_cap(self):
        changes = [_change(i, f"area{i}") for i in range(15)]
        prompts = build_prompts("acme/widget", changes, "ko")
        self.assertEqual(len(prompts["areas"]), 10)

    def test_250_changes_caps_prompt_at_200_lines(self):
        changes = [_change(i, "src") for i in range(250)]
        prompts = build_prompts("acme/widget", changes, "ko")
        # each change line is on its own line; count lines that look like change rows
        change_lines = [ln for ln in prompts["index"].split("\n") if ln.startswith("2024-06-01")]
        self.assertEqual(len(change_lines), 200)


class RunCliFake:
    def __init__(self, mapping=None, fail_on=None):
        self.mapping = mapping or {}
        self.fail_on = fail_on or set()
        self.calls = []

    def __call__(self, cli, prompt, timeout=120):
        self.calls.append((cli, prompt))
        if prompt in self.fail_on:
            return None
        return "요약:" + prompt[:10]


class SummarizeTest(unittest.TestCase):
    def test_summarize_shape_with_fake_runner(self):
        changes = [_change(1, "src"), _change(2, "docs")]
        prompts = build_prompts("acme/widget", changes, "ko")
        runner = RunCliFake()
        result = summarize("claude", prompts, runner=runner)
        self.assertIn("index", result)
        self.assertTrue(result["index"].startswith("요약:"))
        self.assertIsInstance(result["months"], dict)
        self.assertIsInstance(result["areas"], dict)

    def test_failed_item_is_omitted(self):
        changes = [_change(1, "src")]
        prompts = build_prompts("acme/widget", changes, "ko")
        runner = RunCliFake(fail_on={prompts["index"]})
        result = summarize("claude", prompts, runner=runner)
        self.assertNotIn("index", result)

    def test_progress_called_per_item(self):
        changes = [_change(1, "src")]
        prompts = build_prompts("acme/widget", changes, "ko")
        seen = []
        summarize("claude", prompts, runner=RunCliFake(), progress=seen.append)
        self.assertTrue(len(seen) >= 1)


class AvailableClisTest(unittest.TestCase):
    def test_returns_subset_of_known_names(self):
        clis = available_clis()
        self.assertTrue(set(clis).issubset({"claude", "codex"}))


class RunCliSuppressEnvTest(unittest.TestCase):
    def test_run_cli_passes_suppress_env_to_subprocess(self):
        recorded = {}

        def fake_run(args, cwd, capture_output, text, timeout, stdin, check, env=None):
            recorded["env"] = env
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with mock.patch.object(llm_pass_mod.subprocess, "run", side_effect=fake_run):
            run_cli("claude", "hello")

        self.assertIsNotNone(recorded["env"])
        self.assertEqual(recorded["env"].get("LLM_WIKI_KIT_SUPPRESS"), "1")


if __name__ == "__main__":
    unittest.main()
