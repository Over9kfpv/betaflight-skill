"""Variable validation, and the substring trap in Betaflight's `get`."""
import unittest

from bf_vars import (CliResponseError, UnknownVariableError, assert_valid,
                     check_response, parse_get, validate)


class Validation(unittest.TestCase):
    def test_unknown_name_is_rejected_before_it_is_sent(self):
        problems = validate(["set not_a_real_setting = 3"])
        self.assertTrue(problems)
        self.assertIn("unknown variable", problems[0])

    def test_renamed_variable_points_at_the_replacement(self):
        problems = validate(["set dynamic_idle_min_rpm = 30"])
        self.assertIn("dyn_idle_min_rpm", problems[0])

    def test_bare_command_wrapped_in_set_is_caught(self):
        problems = validate(["set save = 1"])
        self.assertIn("bare CLI command", problems[0])

    def test_value_outside_the_range_is_caught(self):
        problems = validate(["set motor_poles = 9999"])
        self.assertTrue(problems)
        self.assertIn("outside the", problems[0])

    def test_valid_commands_pass(self):
        self.assertEqual(
            validate(["get craft_name", "set motor_poles = 12", "status"]), [])

    def test_assert_valid_raises(self):
        with self.assertRaises(UnknownVariableError):
            assert_valid(["set nonsense_name = 1"])


class Parsing(unittest.TestCase):
    def test_parse_get_matches_the_exact_name(self):
        """`get craft_name` also returns osd_craft_name_pos, listed first."""
        reply = ("osd_craft_name_pos = 395\nDefault value: 0\n\n"
                 "craft_name = WHOOP\nDefault value: 0")
        self.assertEqual(parse_get(reply, "craft_name"), "WHOOP")

    def test_parse_get_returns_none_when_absent(self):
        self.assertIsNone(parse_get("some other output", "craft_name"))

    def test_rejection_in_the_reply_raises(self):
        with self.assertRaises(CliResponseError):
            check_response("set foo = 1", "Invalid name")

    def test_clean_reply_passes_the_check(self):
        check_response("get craft_name", "craft_name = WHOOP")


if __name__ == "__main__":
    unittest.main()
