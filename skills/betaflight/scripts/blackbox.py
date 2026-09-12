#!/usr/bin/env python3
"""Manage Betaflight blackbox recordings on onboard SPI flash.

The full cycle is: check how much flash is used, reboot the board into USB mass
storage mode, copy the .bbl files off, decode them, analyze them, and erase the
flash so the next flight has room.

Two of those steps cannot be automated away. `msc` reboots the board as a USB
drive, and returning it to normal mode needs a physical replug. The script says
so at each point rather than pretending otherwise.

Usage:
    python scripts/blackbox.py info
    python scripts/blackbox.py msc
    python scripts/blackbox.py copy --out logs/
    python scripts/blackbox.py decode logs/btfl_001.bbl
    python scripts/blackbox.py analyze logs/btfl_001.01.csv
    python scripts/blackbox.py erase
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys

from fc_serial import CliSession, find_fc_port

VOLUME_LABEL = "BETAFLT"


# -- flash -----------------------------------------------------------------

def flash_info(port=None):
    """Print `flash_info`: sector geometry, total size, and bytes used.

    Betaflight stops logging when the flash fills. It does not wrap around and
    overwrite. Once usedSize reaches totalSize every later flight records
    nothing, so this number is the one that matters.
    """
    with CliSession(port=port) as fc:
        output = fc.run("flash_info")

    print(output)

    used, total = _parse_flash_usage(output)
    if used is not None and total:
        percent = used / total * 100
        print(f"\nUsed {used:,} of {total:,} bytes ({percent:.1f}%).")
        if percent >= 90:
            print("The flash is nearly full. Copy the logs off and erase it, "
                  "or the next flight records nothing.")
    return output


def _parse_flash_usage(output):
    used = total = None
    for line in output.splitlines():
        if "totalSize=" in line:
            total = _int_after(line, "totalSize=")
        if "usedSize =" in line or "usedSize=" in line:
            used = _int_after(line, "usedSize =") or _int_after(line, "usedSize=")
    return used, total


def _int_after(line, marker):
    if marker not in line:
        return None
    tail = line.split(marker, 1)[1].strip()
    digits = ""
    for char in tail:
        if char.isdigit():
            digits += char
        else:
            break
    return int(digits) if digits else None


def erase_flash(port=None, assume_yes=False):
    """Erase the blackbox flash.

    `flash_erase` acts on the chip immediately and needs no EEPROM write, so
    the session ends with `exit noreboot` and the board stays available.
    """
    if not assume_yes:
        print("This erases every flight recording on the onboard flash.")
        if input("Type 'erase' to continue: ").strip().lower() != "erase":
            print("Aborted. Nothing was erased.")
            return

    print("# Erasing... on a large chip this takes up to a minute.", file=sys.stderr)
    with CliSession(port=port) as fc:
        print(fc.run("flash_erase", timeout=120.0))
    print("[+] Blackbox flash erased.")


def mass_storage(port=None):
    """Reboot the board into USB mass storage mode."""
    print("# Rebooting into USB mass storage mode...", file=sys.stderr)
    try:
        with CliSession(port=port) as fc:
            fc.finish("msc", settle=3.0)
    except Exception as err:
        # The board drops the USB link on `msc`, so a read error here is normal.
        print(f"# (link dropped during reboot: {err})", file=sys.stderr)

    print()
    print(f"[!] The board is now a USB drive labelled '{VOLUME_LABEL}'.")
    print("    CLI and MSP will not answer until you physically replug USB.")
    print("    Next: python scripts/blackbox.py copy")


# -- pulling logs ----------------------------------------------------------

def find_volume():
    """Locate the mounted BETAFLT volume on Linux, macOS, or Windows."""
    system = platform.system()

    if system == "Linux":
        user = os.environ.get("USER", "")
        for path in (f"/run/media/{user}/{VOLUME_LABEL}",
                     f"/media/{user}/{VOLUME_LABEL}",
                     f"/media/{VOLUME_LABEL}"):
            if os.path.exists(path):
                return path
        try:
            out = subprocess.check_output(
                ["lsblk", "-o", "NAME,LABEL,MOUNTPOINT"], text=True)
            for line in out.splitlines():
                if VOLUME_LABEL in line:
                    parts = line.split()
                    if len(parts) >= 3 and parts[-1].startswith("/"):
                        return parts[-1]
        except Exception:
            pass

    elif system == "Darwin":
        path = f"/Volumes/{VOLUME_LABEL}"
        if os.path.exists(path):
            return path

    elif system == "Windows":
        try:
            import string
            from ctypes import create_unicode_buffer, windll

            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if not os.path.exists(drive):
                    continue
                name = create_unicode_buffer(1024)
                if windll.kernel32.GetVolumeInformationW(
                        drive, name, 1024, None, None, None, None, 0):
                    if name.value.upper() == VOLUME_LABEL:
                        return drive
        except Exception:
            pass

    return None


def copy_logs(out_dir="logs"):
    """Copy btfl_*.bbl files off the mounted BETAFLT volume."""
    mount = find_volume()
    if not mount:
        print(f"ERROR: no volume labelled '{VOLUME_LABEL}' is mounted.")
        print("  - Run `python scripts/blackbox.py msc` first.")
        if platform.system() == "Linux":
            print("  - If it is attached but unmounted: udisksctl mount -b /dev/sdX1")
        sys.exit(1)

    print(f"Found {VOLUME_LABEL} at {mount}")
    os.makedirs(out_dir, exist_ok=True)

    copied = 0
    for root, _, files in os.walk(mount):
        for name in files:
            # btfl_all.bbl is the concatenation of the numbered files; skip it.
            if name.startswith("btfl_") and name.endswith(".bbl") and name != "btfl_all.bbl":
                source = os.path.join(root, name)
                target = os.path.join(out_dir, name)
                print(f"  {name} -> {target}")
                shutil.copy2(source, target)
                copied += 1

    if not copied:
        print(f"No btfl_*.bbl files on the {VOLUME_LABEL} volume.")
        return

    print(f"\n[+] Copied {copied} log file(s) into {out_dir}/.")
    print("    Replug the USB cable to leave mass storage mode.")
    print("    Then erase the flash: python scripts/blackbox.py erase")


def decode(bbl_path):
    """Decode a .bbl into CSV with the external blackbox_decode tool."""
    decoder = shutil.which("blackbox_decode")
    if not decoder:
        print("ERROR: blackbox_decode is not installed.")
        print("  Arch     : paru -S blackbox-tools-git")
        print("  macOS    : brew install blackbox-tools")
        print("  Source   : https://github.com/betaflight/blackbox-tools")
        sys.exit(1)

    if not os.path.exists(bbl_path):
        print(f"ERROR: file not found: {bbl_path}")
        sys.exit(1)

    print(f"Decoding {bbl_path}...")
    subprocess.run([decoder, bbl_path], check=False)
    print("\nblackbox_decode writes one CSV per flight, named <stem>.NN.csv.")
    print("Analyze it with: python scripts/analyze_log.py <file>.01.csv")


def main():
    parser = argparse.ArgumentParser(
        description="Manage Betaflight blackbox recordings on onboard SPI flash.")
    parser.add_argument("--port", "-p", help="Serial port device")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("info", help="Show flash capacity and how much is used")

    erase = sub.add_parser("erase", help="Erase the blackbox flash")
    erase.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation")

    sub.add_parser("msc", help="Reboot the board into USB mass storage mode")

    copy_cmd = sub.add_parser("copy", help="Copy .bbl files off the BETAFLT volume")
    copy_cmd.add_argument("--out", default="logs", help="Destination directory (default: logs)")

    decode_cmd = sub.add_parser("decode", help="Decode a .bbl file to CSV")
    decode_cmd.add_argument("bbl", help="Path to the .bbl file")

    analyze_cmd = sub.add_parser("analyze", help="Report motor balance and gyro noise")
    analyze_cmd.add_argument("csv", help="Path to a decoded .csv file")
    analyze_cmd.add_argument("--markdown", action="store_true",
                             help="Emit the Markdown block for a flight log entry")

    args = parser.parse_args()

    if args.command == "info":
        flash_info(args.port)
    elif args.command == "erase":
        erase_flash(args.port, assume_yes=args.yes)
    elif args.command == "msc":
        mass_storage(args.port)
    elif args.command == "copy":
        copy_logs(args.out)
    elif args.command == "decode":
        decode(args.bbl)
    elif args.command == "analyze":
        import analyze_log
        report = analyze_log.analyze(args.csv)
        if args.markdown:
            print(analyze_log.format_markdown(report))
        else:
            analyze_log.print_report(report)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
