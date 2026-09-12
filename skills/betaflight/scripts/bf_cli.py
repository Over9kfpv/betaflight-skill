#!/usr/bin/env python3
"""Run Betaflight CLI commands over USB serial.

Every command in one invocation runs inside a single CLI session, and the
session always ends with `exit noreboot` or, with --save, `save`. A board left
in CLI mode ignores MSP and refuses the next connection until USB is replugged,
so that discipline is not optional.

`get` and `set` commands are validated against the variable table in
scripts/bf_vars_table.py before being sent, and the board's reply is checked
for a rejection afterwards. Betaflight answers a bad name with an error line
and carries on, so an unchecked typo fails silently.

Usage:
    python scripts/bf_cli.py "status" "get osd_profile"
    python scripts/bf_cli.py --save "set craft_name = WHOOP"
    python scripts/bf_cli.py --port /dev/ttyACM0 "diff all"
    python scripts/bf_cli.py --serial 3057397C3235 "version"
"""
import argparse
import sys

from bf_vars import CliResponseError, UnknownVariableError, assert_valid, check_response
from fc_serial import CliSession, find_fc_port

WRITE_WARNING = """\
[!] --save writes EEPROM and reboots the board.
[!] REMOVE ALL PROPELLERS before changing motor, DShot, or arming settings.
"""


def execute(commands, port=None, save=False, skip_validation=False):
    """Run commands in one session, printing each reply. Returns the replies."""
    if not skip_validation:
        assert_valid(commands)

    with CliSession(port=port, save=save) as fc:
        results = fc.run_many(commands)

    failures = []
    for command, output in results.items():
        print(f"===== {command} =====")
        print(output)
        print()
        try:
            check_response(command, output)
        except CliResponseError as err:
            failures.append(str(err))

    if failures:
        print("Rejected by the firmware:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        sys.exit(1)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Run Betaflight CLI commands over USB serial.")
    parser.add_argument("commands", nargs="*",
                        help="CLI commands, e.g. 'status' 'get craft_name'")
    parser.add_argument("--save", action="store_true",
                        help="End with `save`, writing EEPROM and rebooting")
    parser.add_argument("--port", "-p", help="Serial port device")
    parser.add_argument("--serial", "-s", help="USB serial number of the board")
    parser.add_argument("--no-validate", action="store_true",
                        help="Send get/set commands without checking the variable table")
    args = parser.parse_args()

    if not args.commands:
        parser.print_help()
        sys.exit(0)

    if args.save:
        print(WRITE_WARNING, file=sys.stderr)

    port = find_fc_port(target_port=args.port, target_serial=args.serial)

    try:
        execute(args.commands, port=port, save=args.save,
                skip_validation=args.no_validate)
    except UnknownVariableError as err:
        print(err, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
