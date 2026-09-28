import unittest

from kit.workflow import rules_block


class RulesBlockTests(unittest.TestCase):
    def test_insert_remove_restores_original_text(self):
        for original in ("", "user text\n", "user text", "a\n\n"):
            with self.subTest(original=original):
                self.assertEqual(rules_block.remove(*rules_block.insert(original, "B", "0.2.0")), original)

    def test_insert_replaces_existing_block_in_place(self):
        original = "before\n"
        text, _ = rules_block.insert(original, "old body", "0.1.0")
        text += "after\n"
        updated, added_newline = rules_block.insert(text, "new body", "0.2.0")
        self.assertFalse(added_newline)
        self.assertEqual(len(rules_block.START_RE.findall(updated)), 1)
        self.assertEqual(updated, "before\n\n<!-- keel:workflow:start 0.2.0 -->\nnew body\n"
                         "<!-- keel:workflow:end -->\nafter\n")

    def test_remove_preserves_separator_before_following_content(self):
        text, added_newline = rules_block.insert("theirs", "B", "0.2.0")
        self.assertEqual(rules_block.remove(text + "- note\n", added_newline), "theirs\n- note\n")

    def test_start_without_end_is_malformed(self):
        text = "before\n<!-- keel:workflow:start 0.2.0 -->\nbody\n"
        with self.assertRaises(rules_block.BlockError):
            rules_block.insert(text, "B", "0.2.0")
        with self.assertRaises(rules_block.BlockError):
            rules_block.remove(text)

    def test_two_blocks_are_malformed(self):
        block = "<!-- keel:workflow:start 0.2.0 -->\nB\n<!-- keel:workflow:end -->\n"
        text = block + block
        with self.assertRaises(rules_block.BlockError):
            rules_block.insert(text, "C", "0.2.0")
        with self.assertRaises(rules_block.BlockError):
            rules_block.remove(text)
