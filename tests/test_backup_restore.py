"""A backup is a replayable script; restoring one must not scaffold it twice."""
import unittest

from backup_restore import extract_commands

SELF_CONTAINED = """\
# Betaflight / STM32G47X
batch start
defaults nosave
set craft_name = WHOOP
batch end
save
"""


class Extraction(unittest.TestCase):
    def test_comments_are_dropped_and_commands_kept(self):
        commands = extract_commands(SELF_CONTAINED)
        self.assertEqual(commands[0], "batch start")
        self.assertEqual(commands[-1], "save")
        self.assertFalse([c for c in commands if c.startswith("#")])

    def test_a_diff_all_carries_its_own_reset_and_save(self):
        """The restore path checks for exactly this before adding its own."""
        commands = extract_commands(SELF_CONTAINED)
        self.assertIn("defaults nosave", commands)
        self.assertEqual(commands[-1], "save")

    def test_a_bare_command_list_has_no_scaffolding(self):
        commands = extract_commands("set craft_name = WHOOP\nset motor_poles = 12\n")
        self.assertNotIn("defaults nosave", commands)
        self.assertNotEqual(commands[-1], "save")


if __name__ == "__main__":
    unittest.main()
