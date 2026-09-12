# Troubleshooting

## No CLI prompt came back

The board did not answer the bare `#` handshake within six seconds.

1. **It is already in CLI mode** from an interrupted session. A board in CLI
   mode does not re-enter CLI mode. Replug the USB cable.
2. **It is in DFU or mass storage mode.** Neither is a normal serial port.
   Replug the USB cable.
3. **The cable is power-only.** Many charging cables carry no data lines. The
   board powers up and its lights come on, but no port appears. Try another
   cable before anything else.
4. **Another program holds the port.** Betaflight Configurator, a serial
   terminal, or another copy of a script. Close it.

Replugging USB is the universal fix. There is no software way to recover a
board stuck in CLI mode, because the thing that would recover it is the thing
that is being ignored.

## Permission denied on the serial port (Linux)

The user is not in the group that owns the tty device. On most distributions:

```bash
sudo usermod -a -G uucp $USER      # Arch
sudo usermod -a -G dialout $USER   # Debian, Ubuntu, Fedora
```

Log out and back in for the group change to take effect. Check which group
owns the device with `ls -l /dev/ttyACM0`.

ModemManager also grabs new serial devices on some distributions and holds
them for several seconds. If the first connection attempt fails and the second
succeeds, that is what happened.

## MSP times out but the CLI works

The board is in CLI mode. CLI mode does not answer MSP binary frames. Run

```bash
python scripts/bf_cli.py "status"
```

which leaves CLI cleanly on the way out, then retry MSP.

## The board disappears from the port list

- After `save`, the board reboots. The port goes away and comes back within a
  few seconds under the same name. Wait, then retry.
- After `msc`, the board comes back as a USB mass storage device, not a serial
  port. That is intended. Replug to return it to normal.
- After `bl` or `dfu`, it is in the bootloader. Replug.

## An EdgeTX radio shows up as a flight controller

EdgeTX handsets use the same STM32 virtual COM port IDs as a flight controller,
`0483:5740`. `fc_connect.py` reads the USB description to tell them apart, but a
radio with an unusual descriptor can still slip through. Pass the board's USB
serial number explicitly:

```bash
python scripts/fc_connect.py --no-probe        # read the serial numbers
python scripts/bf_cli.py --serial 3057397C3235 "status"
```

## A setting does not stick

Betaflight accepts a `set`, then clamps or overrides the value. Common causes:

- The value is outside the firmware's range and was silently clamped.
- The setting belongs to a PID profile or rate profile other than the active
  one. Select the profile first with `profile 1` or `rateprofile 1`.
- The session ended with `exit noreboot`, which does not write EEPROM. Only
  `save` persists a change.

`bf_params.py --set` reads every value back after saving and reports any that
did not take.

## A flight recorded nothing

Check the flash first:

```bash
python scripts/blackbox.py info
```

If `usedSize` equals `totalSize`, the flash is full. Betaflight stops logging
at that point and does not overwrite older logs. Copy the logs off, then erase.

Other causes: `blackbox_device` set to `NONE` or `SERIAL` instead of `SPIFLASH`,
or `blackbox_mode` set to arm-only with a switch that was never flipped.

```bash
python scripts/bf_params.py --get blackbox_device blackbox_mode blackbox_sample_rate
```

## blackbox_decode is missing

It is a separate C utility from Betaflight's blackbox-tools, not a Python
package.

- Arch: `paru -S blackbox-tools-git`
- macOS: `brew install blackbox-tools`
- Source: https://github.com/betaflight/blackbox-tools

The Betaflight Blackbox Explorer desktop app decodes logs too, if a GUI is
acceptable.
