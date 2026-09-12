#!/usr/bin/env python3
"""Read live telemetry and RC channels over MSP v1.

MSP is the binary protocol Betaflight Configurator speaks. It reads live data
without putting the board into CLI mode, so use it for anything you want to
watch while the board is running: stick positions, arming state, link status.

The board must NOT be in CLI mode. If MSP times out, run
`python scripts/bf_cli.py "status"` to leave CLI cleanly, or replug USB.

Usage:
    python scripts/bf_msp.py
    python scripts/bf_msp.py --samples 20 --interval 0.1
    python scripts/bf_msp.py --port /dev/ttyACM0
"""
import argparse
import struct
import sys
import time

import serial

from fc_serial import (BAUD, MSP_API_VERSION, MSP_FC_VARIANT, MSP_RC, MSP_STATUS,
                       find_fc_port, msp_request)


def channel_names(count):
    names = ["Roll", "Pitch", "Yaw", "Thr"]
    return names[:count] + [f"AUX{i}" for i in range(1, max(0, count - 3))]


def main():
    parser = argparse.ArgumentParser(
        description="Read live flight controller telemetry and RC channels over MSP v1.")
    parser.add_argument("--samples", "-n", type=int, default=1,
                        help="Number of RC samples to read (default: 1)")
    parser.add_argument("--interval", "-i", type=float, default=0.2,
                        help="Seconds between samples (default: 0.2)")
    parser.add_argument("--port", "-p", help="Serial port device")
    parser.add_argument("--serial", "-s", help="USB serial number of the board")
    args = parser.parse_args()

    port = find_fc_port(target_port=args.port, target_serial=args.serial)
    print(f"# Opening MSP on {port}...", file=sys.stderr)

    try:
        link = serial.Serial(port, BAUD, timeout=0.2)
    except serial.SerialException as err:
        raise SystemExit(f"Failed to open {port}: {err}")

    with link:
        link.dtr = True
        time.sleep(0.4)

        payload, err = msp_request(link, MSP_API_VERSION)
        if err:
            print(f"MSP_API_VERSION : failed, {err}")
            print("\nThe board is probably stuck in CLI mode. Run "
                  '`python scripts/bf_cli.py "status"` or replug USB.')
            sys.exit(1)
        version = (f"{payload[0]}.{payload[1]}.{payload[2]}"
                   if len(payload) >= 3 else payload.hex())
        print(f"MSP_API_VERSION : {version}")

        payload, err = msp_request(link, MSP_FC_VARIANT)
        if not err and payload:
            print(f"MSP_FC_VARIANT  : {payload.decode('ascii', 'replace')}")

        payload, err = msp_request(link, MSP_STATUS)
        print(f"MSP_STATUS      : {'failed, ' + err if err else 'OK'}")

        print(f"\nRC channels ({args.samples} sample(s)):")
        for index in range(args.samples):
            payload, err = msp_request(link, MSP_RC)
            if err:
                print(f"  sample {index + 1}: failed, {err}")
                break

            channels = struct.unpack("<" + "H" * (len(payload) // 2), payload)
            if index == 0:
                print("  " + "  ".join(f"{n:>6}" for n in channel_names(len(channels))))
            print("  " + "  ".join(f"{v:>6}" for v in channels))

            if index < args.samples - 1:
                time.sleep(args.interval)


if __name__ == "__main__":
    main()
