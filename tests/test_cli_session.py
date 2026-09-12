"""The CLI session must never leave a board stuck in CLI mode."""
import unittest

from fake_fc import FakeFlightController
from fc_serial import CliError, CliSession


def session(fake, **kwargs):
    return CliSession(port="FAKE", serial_factory=fake.as_factory(),
                      delay_scale=0, verbose=False, **kwargs)


class ExitDiscipline(unittest.TestCase):
    def test_exits_cli_on_the_way_out(self):
        fake = FakeFlightController()
        with session(fake) as fc:
            fc.run("status")
        self.assertEqual(fake.commands[-1], "exit noreboot")
        self.assertTrue(fake.closed)

    def test_exits_cli_even_when_the_body_raises(self):
        fake = FakeFlightController()
        with self.assertRaises(ValueError):
            with session(fake) as fc:
                fc.run("status")
                raise ValueError("something went wrong mid-session")
        self.assertEqual(fake.commands[-1], "exit noreboot")

    def test_save_replaces_the_exit_command(self):
        fake = FakeFlightController()
        with session(fake, save=True) as fc:
            fc.run("set craft_name = WHOOP")
        self.assertEqual(fake.commands[-1], "save")

    def test_finish_suppresses_the_later_exit(self):
        """`msc` reboots the board, so no exit command may follow it."""
        fake = FakeFlightController()
        with session(fake) as fc:
            fc.finish("msc", settle=0)
        self.assertEqual(fake.commands[-1], "msc")


class Handshake(unittest.TestCase):
    def test_no_prompt_is_an_error_not_a_hang(self):
        fake = FakeFlightController(prompt_on_handshake=False)
        with self.assertRaises(CliError) as caught:
            with session(fake):
                pass
        self.assertIn("No CLI prompt", str(caught.exception))
        self.assertTrue(fake.closed)

    def test_open_port_is_not_leaked_when_the_handshake_fails(self):
        fake = FakeFlightController(prompt_on_handshake=False)
        with self.assertRaises(CliError):
            with session(fake):
                pass
        self.assertTrue(fake.closed)


class Replies(unittest.TestCase):
    def test_reply_excludes_the_echo_and_the_prompt(self):
        fake = FakeFlightController(responses={"version": "Betaflight 4.5.1"})
        with session(fake) as fc:
            self.assertEqual(fc.run("version"), "Betaflight 4.5.1")

    def test_run_many_keeps_the_order_asked_for(self):
        fake = FakeFlightController()
        with session(fake) as fc:
            results = fc.run_many(["version", "status"])
        self.assertEqual(list(results), ["version", "status"])


if __name__ == "__main__":
    unittest.main()
