#!/usr/bin/env python3
"""Find the flight controller and prove the link works.

Run this before anything else. It lists every serial port, says which one
looks like a Betaflight board, reports which optional external tools are
installed, and then opens a real CLI session to confirm the board answers.

Usage:
    python scripts/fc_connect.py
    python scripts/fc_connect.py --no-probe     # enumerate ports only
    python scripts/fc_connect.py --port /dev/ttyACM0
"""
import argparse
import platform
import shutil
import sys

from fc_serial import CliSession, CliError, describe_ports, find_fc_port

OPTIONAL_TOOLS = {
    "blackbox_decode": "decodes .bbl logs to CSV (package: blackbox-tools)",
    "udisksctl": "mounts the BETAFLT drive on Linux",
    "diskutil": "mounts the BETAFLT drive on macOS",
}


def report_ports():
    candidates, others = describe_ports()

    print("=" * 60)
    print(f"  Betaflight connection check -- {platform.system()} "
          f"({platform.machine()}), Python {platform.python_version()}")
    print("=" * 60)
    print()

    if candidates:
        print(f"Flight controller candidates ({len(candidates)}):")
        for index, info in enumerate(candidates, 1):
            print(f"  [{index}] {info['device']}")
            print(f"      description : {info['description']}")
            print(f"      USB serial  : {info['serial_number']}")
            print(f"      VID:PID     : {info['vid_pid']}")
            print(f"      notes       : {info['notes']}")
        print()
    else:
        print("No flight controller detected.")
        print("  - The USB cable may be power-only. Try another one.")
        print("  - A board in DFU or mass storage mode is not a serial port.")
        print("    Replug the USB cable to return it to normal mode.")
        print()

    if others:
        print(f"Other serial devices ({len(others)}):")
        for info in others:
            print(f"  - {info['device']} ({info['description']}) -- {info['notes']}")
        print()

    print("Optional external tools:")
    for name, purpose in OPTIONAL_TOOLS.items():
        path = shutil.which(name)
        state = f"found at {path}" if path else "not installed"
        print(f"  - {name:<16}: {state}  ({purpose})")
    print()

    return candidates


def probe(port=None):
    """Open a CLI session and read the board's identity."""
    print("Probing the board over CLI...")
    try:
        with CliSession(port=port) as fc:
            results = fc.run_many(["version", "status"])
    except (CliError, SystemExit) as err:
        print(f"  FAILED: {err}")
        return False

    for command, output in results.items():
        print(f"\n===== {command} =====")
        print(output)

    print("\nLink confirmed. The board answered and was left out of CLI mode.")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Detect the flight controller and verify the CLI link.")
    parser.add_argument("--port", "-p", help="Serial port device")
    parser.add_argument("--no-probe", action="store_true",
                        help="List ports without opening a CLI session")
    args = parser.parse_args()

    candidates = report_ports()

    if args.no_probe:
        return
    if not candidates and not args.port:
        sys.exit(1)

    port = args.port or find_fc_port()
    if not probe(port):
        sys.exit(1)


if __name__ == "__main__":
    main()
