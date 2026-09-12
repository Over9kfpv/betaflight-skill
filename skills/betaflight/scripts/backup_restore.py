#!/usr/bin/env python3
"""Back up and restore the whole flight controller configuration.

A backup is Betaflight's own `diff all` output, captured to a timestamped
file. That output is already a complete restore script: it opens with
`batch start`, resets the board with `defaults nosave`, and ends with `save`.

Take one before any tuning session. It is the only way back from a
configuration you cannot undo by hand.

Usage:
    python scripts/backup_restore.py --backup --name before-pid-session
    python scripts/backup_restore.py --list
    python scripts/backup_restore.py --restore backups/backup_2026-09-12_101500.txt
"""
import argparse
import datetime
import os
import sys

from bf_vars import check_response
from fc_serial import CliSession


DEFAULT_BACKUP_DIR = "backups"


def get_backup_dir(path=None):
    """Where backups live. Relative to the working directory, not the skill.

    The skill is installed read-only under the agent's skills directory, so
    backups belong beside the project being worked on instead.
    """
    backup_dir = path or os.environ.get("BETAFLIGHT_BACKUP_DIR", DEFAULT_BACKUP_DIR)
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def create_backup(name=None, port=None, backup_dir=None):
    """Capture `diff all` to a timestamped, replayable file."""
    backup_dir = get_backup_dir(backup_dir)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    suffix = f"_{name}" if name else ""
    filename = f"backup_{now_str}{suffix}.txt"
    filepath = os.path.join(backup_dir, filename)

    with CliSession(port=port) as fc:
        print(f"# Fetching `diff all` from {fc.port}...", file=sys.stderr)
        diff_text = fc.run("diff all", timeout=90.0)
        resolved_port = fc.port

    check_response("diff all", diff_text)

    commands = extract_commands(diff_text)
    if not commands:
        print("ERROR: The flight controller returned no configuration commands.")
        print("       Nothing was written. Check the connection and retry.")
        sys.exit(1)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Betaflight configuration backup created {now_str}\n")
        f.write(f"# Port: {resolved_port}\n")
        f.write(f"# Restore with: python scripts/backup_restore.py --restore {filename}\n")
        f.write("#\n")
        f.write(diff_text.rstrip() + "\n")

    print(f"[+] Backup saved to `{filepath}` ({len(commands)} commands).")
    return filepath


def extract_commands(text):
    """Return the replayable CLI commands from a diff, dropping comments.

    Betaflight comments start with '#'. Everything else in a `diff all` body
    is a command, including `batch start`, `board_name`, and `profile`.
    """
    commands = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        commands.append(stripped)
    return commands


def list_backups(backup_dir=None):
    backup_dir = get_backup_dir(backup_dir)
    files = sorted([f for f in os.listdir(backup_dir) if f.endswith(".txt")], reverse=True)

    print("==================================================")
    print("  Saved flight controller backups")
    print("==================================================")
    if not files:
        print(f"  No backups found in {backup_dir}/.")
    else:
        for f in files:
            size = os.path.getsize(os.path.join(backup_dir, f))
            print(f"  • {f:<35} ({size} bytes)")
    print("==================================================\n")


def restore_backup(filepath, port=None, assume_yes=False, backup_dir=None):
    """Replay a backup onto the flight controller.

    Resets to defaults first. Without that step a restore only overlays the
    saved values, leaving any setting changed since the backup in place.
    """
    if not os.path.exists(filepath):
        backup_dir = get_backup_dir(backup_dir)
        alt_path = os.path.join(backup_dir, filepath)
        if os.path.exists(alt_path):
            filepath = alt_path
        else:
            print(f"ERROR: Backup file not found: {filepath}")
            sys.exit(1)

    print(f"# Reading backup file: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        commands = extract_commands(f.read())

    if not commands:
        print("ERROR: Backup file contains no valid CLI commands.")
        sys.exit(1)

    print(f"\n[!] This will erase the current configuration and replay "
          f"{len(commands)} commands from the backup.")
    print("[!] Ensure ALL PROPELLERS ARE REMOVED before restoring.")

    if not assume_yes:
        answer = input("Type 'restore' to continue: ").strip().lower()
        if answer != "restore":
            print("Aborted. Nothing was changed.")
            sys.exit(1)

    # Betaflight's own `diff all` output is already a complete restore script:
    # it opens with `batch start`, resets with `defaults nosave`, and ends with
    # `save`. Re-wrapping such a file would reset twice and then send a second
    # `save` to a board that the first one already rebooted. Only wrap a file
    # that lacks its own scaffolding.
    self_contained = "defaults nosave" in commands and commands[-1] == "save"

    if self_contained:
        body, closer = commands[:-1], commands[-1]
    else:
        body, closer = ["defaults nosave"] + commands, "save"

    with CliSession(port=port) as fc:
        print(f"# Restoring {len(commands)} commands to FC on {fc.port}...",
              file=sys.stderr)
        results = fc.run_many(body, timeout=90.0)
        # `save` reboots the board, so no prompt returns; finish() accounts
        # for that and stops the session from sending a further exit command.
        fc.finish(closer)

    rejected = []
    for command, output in results.items():
        if any(m in output for m in ("Invalid name", "Unknown command", "Parse error")):
            rejected.append(f"{command} -> {output.strip().splitlines()[-1]}")

    if rejected:
        print(f"\n[!] {len(rejected)} command(s) were rejected by the firmware:")
        for item in rejected:
            print(f"      {item}")
        print("    The rest of the configuration was restored and saved.")
    else:
        print(f"\n[+] Configuration restored from `{filepath}` and saved.")


def main():
    parser = argparse.ArgumentParser(description="Back up and restore the flight controller configuration.")
    parser.add_argument("--backup", action="store_true", help="Create a timestamped CLI configuration backup")
    parser.add_argument("--name", help="Optional name label for backup file")
    parser.add_argument("--list", action="store_true", help="List all saved configuration backups")
    parser.add_argument("--restore", metavar="FILE.txt", help="Restore CLI configuration from backup file")
    parser.add_argument("--port", help="Serial port device")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip the restore confirmation prompt")
    parser.add_argument("--dir", help=f"Backup directory (default: {DEFAULT_BACKUP_DIR}/)")

    args = parser.parse_args()

    if args.backup:
        create_backup(name=args.name, port=args.port, backup_dir=args.dir)
    elif args.restore:
        restore_backup(args.restore, port=args.port, assume_yes=args.yes,
                       backup_dir=args.dir)
    else:
        list_backups(args.dir)


if __name__ == "__main__":
    main()
