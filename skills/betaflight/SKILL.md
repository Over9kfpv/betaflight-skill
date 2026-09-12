---
name: betaflight
description: Connect to a Betaflight flight controller over USB serial and work with it end to end - detect the board, run CLI commands, read and write settings by name with validation, back up and restore the whole configuration, read live MSP telemetry, and pull, decode and analyze blackbox flight logs. Use for any request mentioning Betaflight, a flight controller, FPV quad or whoop, the Betaflight CLI, PID or filter settings, blackbox logs, motor balance, or gyro noise.
license: MIT
---

# Betaflight flight controller

Talk to a Betaflight board directly over USB serial. No Configurator, no MCP
server, no build step. Every script here is Python with one dependency,
pyserial, and runs on Windows, macOS and Linux.

## Before anything else

**Propellers off.** Any command that touches motors, DShot, arming or mixer
settings can spin a motor. Ask the user to confirm props are removed before
sending one.

**Take a backup before a tuning session.** A `diff all` capture is the only
reliable way back:

```bash
python scripts/backup_restore.py --backup --name before-tuning
```

## The one rule that breaks boards

A Betaflight board in CLI mode answers nothing else. No MSP, no new CLI
connection, until the USB cable is physically replugged. Every session must
end with `exit noreboot` or `save`.

`scripts/fc_serial.py` enforces this: `CliSession` sends the exit command from
a `finally` block, so it runs even when the body raises. Use that class, or the
scripts built on it. Never open a raw serial port and type CLI commands into it.

If the board has gone unresponsive, replugging USB is the fix, and the only fix.

## Workflow

### 1. Find the board and prove the link

```bash
python scripts/fc_connect.py
```

Lists every serial port, marks which look like a flight controller, warns when
one is an EdgeTX radio sharing the same USB IDs, reports whether the optional
`blackbox_decode` tool is installed, then opens a real CLI session and reads
`version` and `status`.

A board in DFU or mass storage mode is not a serial port at all. Replug USB to
bring it back.

### 2. Run CLI commands

```bash
python scripts/bf_cli.py "status" "version" "diff all"
python scripts/bf_cli.py --save "set craft_name = WHOOP"
python scripts/bf_cli.py --port /dev/ttyACM0 "get osd_profile"
```

All commands in one invocation share a single session. `get` and `set` are
checked against the variable table in `scripts/bf_vars_table.py` before being
sent, and replies are checked for rejections afterwards. Betaflight answers a
bad name with an error line and carries on, so an unchecked typo fails silently.

### 3. Read and write settings by name

Prefer this over raw `set` for configuration changes. It validates the value
against the variable's range or enum, writes, saves, and reads every value back
to confirm the board took it.

```bash
python scripts/bf_params.py --get craft_name dshot_bidir motor_poles
python scripts/bf_params.py --set dyn_idle_min_rpm=30 p_pitch=45
python scripts/bf_params.py --set gyro_lpf1_static_hz=250 --dry-run
python scripts/bf_params.py --list blackbox
```

`--dry-run` validates without touching the board. `--list` searches the
variable table by substring and prints each match's type and allowed range.

### 4. Read live telemetry

```bash
python scripts/bf_msp.py --samples 20 --interval 0.1
```

MSP reads live data without entering CLI mode: API version, arming state, and
RC channel values. The board must not be in CLI mode. If MSP times out, run
`python scripts/bf_cli.py "status"` to exit CLI cleanly, then retry.

### 5. Pull and analyze blackbox logs

The full cycle, in order:

```bash
python scripts/blackbox.py info       # how full is the flash
python scripts/blackbox.py msc        # reboot as a USB drive
# --- physically replug USB is NOT needed yet; the drive mounts now ---
python scripts/blackbox.py copy --out logs/
# --- now replug USB to leave mass storage mode ---
python scripts/blackbox.py decode logs/btfl_001.bbl
python scripts/blackbox.py analyze logs/btfl_001.01.csv
python scripts/blackbox.py erase      # make room for the next flight
```

**Erase the flash after every download.** Betaflight stops logging when the
flash fills. It does not wrap around and overwrite old logs, so once the flash
is full every later flight records nothing at all.

`decode` needs the external `blackbox_decode` binary from Betaflight's
blackbox-tools. The script prints install instructions when it is missing.

### 6. Restore a configuration

```bash
python scripts/backup_restore.py --list
python scripts/backup_restore.py --restore backups/backup_2026-09-12_101500.txt
```

A restore resets the board to defaults first, then replays the saved commands,
then saves. Without the reset it would only overlay the saved values and leave
anything changed since the backup in place.

## Reading the numbers

`analyze` reports two metrics. Both are covered in detail in
`references/reading-logs.md`; the short version:

- **Motor balance** is mean eRPM over mean motor output, per motor. Uniform
  ratios mean healthy motors and solder joints. Past 5% deviation from the
  fleet mean, suspect a cold joint, a damaged winding, a worn bearing, or a
  slipping prop. It reads `n/a` when bidirectional DShot was off, because the
  log then carries no eRPM at all.
- **Gyro noise floor** is mean spectral magnitude above 100 Hz per axis. Below
  100 Hz the signal is craft motion; above it, frame resonance and motor noise.
  Compare axes against each other and against past flights, not against an
  absolute number.

A decoder warning that "75% of iterations are missing" is expected. The default
`blackbox_sample_rate` of 1/4 omits three loop iterations in four to save flash.
It is decimation by design, not data loss.

## Working with no hardware

`scripts/fake_fc.py` is a serial stand-in that speaks the Betaflight CLI in
memory. Use it to test changes, or to demonstrate a flow, with nothing plugged
in:

```python
from fake_fc import FakeFlightController
from fc_serial import CliSession

fake = FakeFlightController()
with CliSession(port="FAKE", serial_factory=fake.as_factory(), delay_scale=0) as fc:
    print(fc.run("status"))
assert fake.commands[-1] == "exit noreboot"
```

## Troubleshooting

`references/troubleshooting.md` covers the failure modes worth knowing: no
prompt, permission denied on the serial port, MSP timeouts, the board vanishing
from the port list, and a full flash that silently records nothing.

## Environment variables

The scripts read two, both optional:

- `BETAFLIGHT_BACKUP_DIR` overrides where `backup_restore.py` writes backups.
  It defaults to `backups/` in the working directory.
- `USER` is read on Linux only, to build the `/run/media/$USER/BETAFLT` path
  where the blackbox drive mounts. It falls back to other mount points.

Neither is required and neither carries a secret.

## Scripts

| Script | Purpose |
| --- | --- |
| `fc_connect.py` | Detect the board, verify the CLI link |
| `bf_cli.py` | Run arbitrary CLI commands in one safe session |
| `bf_params.py` | Read and write settings by name, validated and verified |
| `bf_msp.py` | Live telemetry and RC channels over MSP v1 |
| `blackbox.py` | Flash info, mass storage, copy, decode, analyze, erase |
| `analyze_log.py` | Motor balance and gyro noise from a decoded CSV |
| `backup_restore.py` | Capture and replay a full `diff all` configuration |
| `fc_serial.py` | Port discovery, CLI session, MSP framing (library) |
| `bf_vars.py` | Variable validation and reply checking (library) |
| `bf_vars_table.py` | Generated variable table with types and ranges |
| `fake_fc.py` | In-memory board for testing without hardware |

Install pyserial first: `pip install pyserial`. numpy is optional and only
speeds up the FFT in `analyze_log.py`.
