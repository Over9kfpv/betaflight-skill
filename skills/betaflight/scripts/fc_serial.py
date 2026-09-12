#!/usr/bin/env python3
"""Serial transport for Betaflight flight controllers.

Everything the other scripts in this skill need to talk to a board lives here:
port discovery across Windows, macOS and Linux, a CLI session that guarantees
exit discipline, and an MSP v1 request helper.

The two protocols are mutually exclusive. A board in CLI mode ignores MSP
binary frames, and a board in normal mode ignores CLI text until the bare `#`
handshake arrives. `CliSession` always leaves the board back in normal mode.

    from fc_serial import CliSession

    with CliSession() as fc:
        print(fc.run("status"))
"""
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # pragma: no cover - environment guard
    print("ERROR: 'pyserial' is not installed. Run: pip install pyserial",
          file=sys.stderr)
    raise

# STM32 virtual COM port. EdgeTX radio handsets share these IDs, so the
# description is checked before a port is accepted as a flight controller.
STM32_VCP_VID = 0x0483
STM32_VCP_PID = 0x5740
STM32_DFU_PID = 0xDF11
STM32_MSC_PID = 0x5720

BAUD = 115200


class CliError(RuntimeError):
    """The flight controller could not enter or complete a CLI session."""


# port discovery

def _is_radio(description):
    lowered = (description or "").lower()
    return any(word in lowered for word in ("radio", "edgetx", "opentx"))


def describe_ports():
    """Classify serial ports as flight controller candidates or not.

    Returns (candidates, others, hidden). `hidden` counts the built-in
    non-USB serial ports, of which a PC can enumerate dozens. None of them can
    be a flight controller, so they are counted rather than listed.
    """
    candidates = []
    others = []
    hidden = 0

    for port in serial.tools.list_ports.comports():
        if port.vid is None:
            # No USB vendor ID means a motherboard UART, not a plugged device.
            hidden += 1
            continue

        description = port.description or ""
        notes = []
        is_fc = False

        if port.vid == STM32_VCP_VID:
            if port.pid == STM32_DFU_PID:
                notes.append("DFU bootloader mode (flash via Betaflight Configurator)")
            elif port.pid == STM32_MSC_PID:
                notes.append("USB mass storage mode (blackbox flash is mounted)")
            elif port.pid == STM32_VCP_PID:
                if _is_radio(description):
                    notes.append("EdgeTX radio handset, not a flight controller")
                else:
                    is_fc = True
                    notes.append("Flight controller (virtual COM port)")

        if not is_fc and not notes and (
            "ACM" in port.device or "usbmodem" in port.device
            or "USB Serial" in description
        ):
            is_fc = True
            notes.append("Likely serial flight controller")

        info = {
            "device": port.device,
            "description": description,
            "serial_number": port.serial_number or "n/a",
            "vid_pid": (f"{port.vid:04x}:{port.pid:04x}"
                        if port.vid and port.pid else "unknown"),
            "notes": ", ".join(notes) or "Generic serial device",
        }
        (candidates if is_fc else others).append(info)

    return candidates, others, hidden


def find_fc_port(target_port=None, target_serial=None):
    """Return the device path of the flight controller to talk to.

    `target_port` wins outright. `target_serial` matches the USB serial number,
    which is how you address one specific board when several are plugged in.
    """
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        raise SystemExit("No serial ports found. Is the flight controller plugged in?")

    if target_port:
        for port in ports:
            if port.device.lower() == target_port.lower():
                return port.device
        # Not enumerated, but the caller may know better than pyserial does.
        return target_port

    if target_serial:
        for port in ports:
            if port.serial_number == target_serial:
                return port.device
        raise SystemExit(
            f"No flight controller with USB serial number '{target_serial}'.")

    candidates, _, _ = describe_ports()
    if candidates:
        return candidates[0]["device"]

    return ports[0].device


# CLI

def read_until_prompt(ser, timeout=30.0, quiet_for=0.3):
    """Read until a `#` prompt arrives and the line has gone quiet."""
    buf = b""
    deadline = time.time() + timeout
    last_recv = time.time()

    while time.time() < deadline:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            last_recv = time.time()
        elif buf.rstrip().endswith(b"#") and (time.time() - last_recv) > quiet_for:
            break
        else:
            time.sleep(0.05)

    return buf.decode("utf-8", "replace")


def _default_serial_factory(port, baud, timeout):
    return serial.Serial(port, baud, timeout=timeout)


class CliSession:
    """One Betaflight CLI session, held open for the whole exchange.

    The session is a context manager so the closing `exit noreboot` (or `save`)
    runs in a `finally` block. A board left in CLI mode refuses every later MSP
    call and every later CLI connection until the USB cable is replugged, so
    that guarantee is the whole point of the class.

    Args:
        port: Device path. Auto-detected when omitted.
        save: End with `save`, writing EEPROM and rebooting, instead of
            `exit noreboot`.
        serial_factory: (port, baud, timeout) -> serial-like object. The test
            suite injects `fake_fc.FakeFlightController` here.
        delay_scale: Multiplies the hardware settle delays. Tests set 0.
    """

    def __init__(self, port=None, save=False, baud=BAUD, timeout=0.2,
                 serial_factory=None, verbose=True, delay_scale=1.0):
        self.port = port
        self.save = save
        self.baud = baud
        self.timeout = timeout
        self.verbose = verbose
        self.delay_scale = delay_scale
        self.quiet_for = 0.3 * delay_scale if delay_scale else 0.0
        self.handshake_timeout = 6.0 * delay_scale if delay_scale else 0.2
        self._serial_factory = serial_factory or _default_serial_factory
        self._ser = None
        self.sent = []
        self.exited = False

    # lifecycle

    def __enter__(self):
        if self.port is None:
            self.port = find_fc_port()

        self._log(f"# Connecting to flight controller on {self.port}...")

        try:
            self._ser = self._serial_factory(self.port, self.baud, self.timeout)
        except Exception as err:
            raise CliError(
                f"Failed to open port {self.port}: {err}\n"
                "Close anything else holding the port: Betaflight Configurator, "
                "a serial terminal, another copy of this script."
            )

        try:
            self._handshake()
        except Exception:
            self._close()
            raise

        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._exit_cli()
        except Exception as err:
            # A failed exit must not mask an exception from the body.
            if exc_type is None:
                raise
            self._log(f"# WARNING: could not send the CLI exit command: {err}")
        finally:
            self._close()
        return False

    def _handshake(self):
        """Enter CLI mode with a bare `#` byte and no line terminator."""
        self._ser.dtr = True
        time.sleep(0.4 * self.delay_scale)
        self._ser.reset_input_buffer()

        self._ser.write(b"#")
        self._ser.flush()

        prompt = read_until_prompt(self._ser, timeout=self.handshake_timeout,
                                   quiet_for=self.quiet_for)
        if "#" not in prompt:
            raise CliError(
                "No CLI prompt came back from the flight controller.\n"
                "  1. Stuck in CLI mode from an interrupted session -> replug USB.\n"
                "  2. In DFU or mass storage mode -> replug USB.\n"
                "  3. Wrong port, or a power-only USB cable."
            )

    def _exit_cli(self):
        if self.exited or self._ser is None:
            return
        command = "save" if self.save else "exit noreboot"
        self._write_line(command)
        time.sleep((1.5 if self.save else 0.5) * self.delay_scale)
        self.exited = True
        if self.save:
            self._log("# Saved to EEPROM. The flight controller is rebooting.")
        else:
            self._log("# Left CLI mode cleanly. The board is ready for MSP.")

    def _close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    # commands

    def run(self, command, timeout=60.0):
        """Send one command and return its output, without echo or prompt."""
        if self._ser is None:
            raise CliError("The CLI session is not open. Use `with CliSession() as fc:`.")

        self._ser.reset_input_buffer()
        self._write_line(command)
        raw = read_until_prompt(self._ser, timeout=timeout, quiet_for=self.quiet_for)
        return self._clean(command, raw)

    def run_many(self, commands, timeout=60.0):
        """Send several commands, returning an ordered {command: output} map."""
        return {cmd: self.run(cmd, timeout=timeout) for cmd in commands}

    def finish(self, command="save", settle=2.0):
        """Send a command that reboots the board, then close the session.

        `save`, `defaults` and `msc` drop the USB link, so no prompt returns.
        Marking the session finished stops `__exit__` writing `exit noreboot`
        down a port that is already going away.
        """
        if self._ser is None:
            raise CliError("The CLI session is not open.")
        self._write_line(command)
        time.sleep(settle * self.delay_scale if self.delay_scale else 0)
        self.exited = True
        self._log(f"# Sent `{command}`. The flight controller is rebooting.")

    # internals

    @staticmethod
    def _clean(command, raw):
        """Strip the echoed command and the trailing prompt from a reply.

        What is left is the command's own output, which is what makes a
        captured `diff all` replayable verbatim as a restore.
        """
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if lines and lines[0].strip() == command.strip():
            lines = lines[1:]
        while lines and lines[-1].strip() in ("#", ""):
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        return "\n".join(lines)

    def _write_line(self, command):
        self.sent.append(command)
        self._ser.write((command + "\r\n").encode("utf-8"))
        self._ser.flush()

    def _log(self, message):
        if self.verbose:
            print(message, file=sys.stderr)


def run_commands(commands, port=None, save=False, serial_factory=None,
                 verbose=True, delay_scale=1.0):
    """Open a session, run commands, return {command: output}."""
    with CliSession(port=port, save=save, serial_factory=serial_factory,
                    verbose=verbose, delay_scale=delay_scale) as fc:
        return fc.run_many(commands)


# MSP v1

MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_STATUS = 101
MSP_RC = 105


def msp_request(ser, code, timeout=2.0):
    """Send one MSP v1 request and return (payload, error).

    A request frame is `$M<`, a payload length, the command code and an XOR
    checksum. A reply is the same with `$M>`; `$M!` means the board rejected
    the command.
    """
    ser.reset_input_buffer()
    ser.write(b"$M<" + bytes([0, code, 0 ^ code]))
    ser.flush()

    deadline = time.time() + timeout
    buf = b""

    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
        else:
            time.sleep(0.01)

        idx = buf.find(b"$M")
        if idx < 0 or len(buf) < idx + 5:
            continue

        if buf[idx + 2:idx + 3] == b"!":
            return None, "the flight controller rejected the request"

        payload_len = buf[idx + 3]
        if len(buf) < idx + 6 + payload_len:
            continue

        resp_code = buf[idx + 4]
        payload = buf[idx + 5:idx + 5 + payload_len]
        crc = buf[idx + 5 + payload_len]

        calculated = payload_len ^ resp_code
        for byte in payload:
            calculated ^= byte
        if crc != calculated:
            return None, "checksum error"

        return payload, None

    return None, "timeout, no MSP reply"
