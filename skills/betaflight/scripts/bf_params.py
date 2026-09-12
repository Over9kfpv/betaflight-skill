#!/usr/bin/env python3
"""Read and write Betaflight settings by name, with verification.

`bf_cli.py` sends whatever you type. This is the safer path for settings: it
checks each name and value against the variable table first, writes them in one
session, and then reads every changed value back to confirm the board actually
took it.

Betaflight's `get` matches on substring and prints every variable containing
the query, alphabetically. `get craft_name` therefore returns
`osd_craft_name_pos` first. Values here are matched on the exact name, so the
reply for the wrong variable is never mistaken for the right one.

Usage:
    python scripts/bf_params.py --get craft_name dshot_bidir motor_poles
    python scripts/bf_params.py --set craft_name=WHOOP dyn_idle_min_rpm=30
    python scripts/bf_params.py --set p_pitch=45 --dry-run
    python scripts/bf_params.py --list blackbox      # names matching a substring
"""
import argparse
import sys

from bf_vars import (VARIABLES, CliResponseError, UnknownVariableError,
                     assert_valid, check_response, parse_get)
from fc_serial import CliSession, find_fc_port

WRITE_WARNING = """\
[!] Settings are written to EEPROM and the board reboots.
[!] REMOVE ALL PROPELLERS before changing motor, DShot, or arming settings.
"""


def describe(name):
    """One line describing a variable's kind and constraint."""
    entry = VARIABLES.get(name)
    if not entry:
        return "unknown variable"
    kind = entry.get("kind")
    if kind == "enum":
        return "enum: " + ", ".join(entry.get("values", []))
    if kind == "int":
        return f"int: {entry.get('min')} to {entry.get('max')}"
    if kind == "string":
        return f"string, up to {entry.get('maxlen')} characters"
    return kind or "unknown kind"


def list_matching(query):
    matches = sorted(n for n in VARIABLES if query.lower() in n.lower())
    if not matches:
        print(f"No variable name contains '{query}'.")
        return
    print(f"{len(matches)} variable(s) matching '{query}':")
    for name in matches:
        print(f"  {name:<40} {describe(name)}")


def read_values(names, port=None):
    """Return {name: value} read from the board. Missing values are None."""
    commands = [f"get {name}" for name in names]
    assert_valid(commands)

    with CliSession(port=port) as fc:
        results = fc.run_many(commands)

    values = {}
    for name, command in zip(names, commands):
        output = results[command]
        check_response(command, output)
        values[name] = parse_get(output, name)
    return values


def write_values(pairs, port=None, verify=True):
    """Apply {name: value}, save, and read each one back.

    Returns {name: (wanted, observed)}. `save` reboots the board, so the
    read-back opens a second session once the board is up again.
    """
    commands = [f"set {name} = {value}" for name, value in pairs.items()]
    assert_valid(commands)

    with CliSession(port=port, save=True) as fc:
        results = fc.run_many(commands)

    problems = []
    for command, output in results.items():
        try:
            check_response(command, output)
        except CliResponseError as err:
            problems.append(str(err))

    if problems:
        print("Rejected by the firmware:", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        sys.exit(1)

    if not verify:
        return {name: (value, None) for name, value in pairs.items()}

    # The board is rebooting after `save`. Wait for the USB link to come back.
    import time
    time.sleep(4.0)

    observed = read_values(list(pairs), port=port)
    return {name: (value, observed.get(name)) for name, value in pairs.items()}


def parse_assignments(items):
    pairs = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--set expects name=value, got '{item}'")
        name, _, value = item.partition("=")
        pairs[name.strip()] = value.strip()
    return pairs


def main():
    parser = argparse.ArgumentParser(
        description="Read and write Betaflight settings by name, with verification.")
    parser.add_argument("--get", nargs="+", metavar="NAME", help="Variables to read")
    parser.add_argument("--set", nargs="+", metavar="NAME=VALUE",
                        help="Variables to write, then save and verify")
    parser.add_argument("--list", metavar="SUBSTRING",
                        help="List known variable names containing a substring")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate names and values without touching the board")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the read-back after saving")
    parser.add_argument("--port", "-p", help="Serial port device")
    parser.add_argument("--serial", "-s", help="USB serial number of the board")
    args = parser.parse_args()

    if args.list:
        list_matching(args.list)
        return

    if not args.get and not args.set:
        parser.print_help()
        return

    try:
        if args.dry_run:
            commands = [f"get {n}" for n in (args.get or [])]
            commands += [f"set {n} = {v}"
                         for n, v in parse_assignments(args.set or []).items()]
            assert_valid(commands)
            print("All names and values are accepted by the variable table:")
            for command in commands:
                print(f"  {command}")
            return

        port = find_fc_port(target_port=args.port, target_serial=args.serial)

        if args.get:
            values = read_values(args.get, port=port)
            for name, value in values.items():
                shown = value if value is not None else "not reported"
                print(f"{name:<40} = {shown}")

        if args.set:
            print(WRITE_WARNING, file=sys.stderr)
            pairs = parse_assignments(args.set)
            outcome = write_values(pairs, port=port, verify=not args.no_verify)

            mismatched = 0
            for name, (wanted, observed) in outcome.items():
                if observed is None:
                    print(f"{name:<40} set to {wanted} (not verified)")
                elif observed.upper() == str(wanted).upper():
                    print(f"{name:<40} = {observed}  confirmed")
                else:
                    mismatched += 1
                    print(f"{name:<40} = {observed}  WANTED {wanted}")
            if mismatched:
                print(f"\n{mismatched} setting(s) did not take. "
                      "The firmware may clamp or override them.", file=sys.stderr)
                sys.exit(1)

    except UnknownVariableError as err:
        print(err, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
