from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from kit.workflow import ladder


class LadderTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)

    def write(self, data):
        text = data if isinstance(data, str) else json.dumps(data)
        (self.home / "ladder.json").write_text(text, encoding="utf-8")

    def assert_refused(self):
        with self.assertRaises(ladder.LadderError) as caught:
            ladder.load(self.home)
        self.assertIn("ladder.json", str(caught.exception))

    def test_missing_file_gives_defaults(self):
        config = ladder.load(self.home)
        self.assertEqual(config.phase_review_limit, 3)
        self.assertEqual(len(config.steps), 2)
        self.assertEqual(config.steps[0].claude, {"model": "sonnet", "effort": "xhigh"})
        self.assertEqual(config.steps[1].codex, {"model": None, "effort": "xhigh"})

    def test_invalid_files_are_refused(self):
        self.write("{not json")
        self.assert_refused()
        bad = copy.deepcopy(ladder.DEFAULTS)
        bad["phase_review_limit"] = 0
        self.write(bad)
        self.assert_refused()
        bad = copy.deepcopy(ladder.DEFAULTS)
        bad["steps"] = []
        self.write(bad)
        self.assert_refused()
        bad = copy.deepcopy(ladder.DEFAULTS)
        del bad["steps"][0]["claude"]
        self.write(bad)
        self.assert_refused()

    def test_next_step_with_defaults(self):
        config = ladder.load(self.home)
        self.assertIs(ladder.next_step(1, config), config.steps[0])
        self.assertIs(ladder.next_step(2, config), config.steps[1])
        self.assertIsNone(ladder.next_step(3, config))

    def test_last_step_repeats(self):
        data = copy.deepcopy(ladder.DEFAULTS)
        data["phase_review_limit"] = 5
        data["steps"].append(copy.deepcopy(data["steps"][1]))
        data["steps"][2]["claude"]["model"] = "fable"
        self.write(data)
        config = ladder.load(self.home)
        self.assertEqual(ladder.next_step(4, config).claude["model"], "fable")


if __name__ == "__main__":
    unittest.main()
