#!/usr/bin/env python3
"""In-memory stand-in for a Betaflight flight controller over serial.

Lets the whole toolchain run, and be tested, with no drone plugged in. It is
duck-typed against the subset of `serial.Serial` that `fc_serial.CliSession`
actually uses: `read`, `write`, `flush`, `dtr`, `reset_input_buffer`, `close`.

    from fake_fc import FakeFlightController
    from fc_serial import CliSession

    fake = FakeFlightController()
    with CliSession(port="FAKE", serial_factory=fake.as_factory(),
                    delay_scale=0) as fc:
        print(fc.run("status"))

    assert fake.commands[-1] == "exit noreboot"

Pass `responses=` to override a reply, or `error_on=` to make a command fail
the way the firmware does.
"""
PROMPT = b"\r\n# "

STATUS_REPLY = """\
MCU G473 Clock=170MHz, Vref=3.30V, Core temp=38degC
Stack size: 2048, Stack address: 0x2001fff0
Config size: 4096, Max available config: 16384
Devices detected: SPI:2, I2C:1
Gyros detected: gyro 1 locked dma
GYRO=ICM42688P, ACC=ICM42688P, BARO=BMP280
OSD: MAX7456
System Uptime: 42 seconds, Current Time: 2026-09-12T10:15:00.000
CPU:11%, cycle time: 125, GYRO rate: 8000, RX rate: 111, System rate: 9
Arming disable flags: RXLOSS CLI"""

VERSION_REPLY = """\
# Betaflight / STM32G47X (G473) 4.5.1 Apr 15 2026 / 16:24:57 (norevision) MSP API: 1.46
# board: manufacturer_id: BEFH, board_name: BETAFPVG473"""

DIFF_ALL_REPLY = """\
# version
# Betaflight / STM32G47X (G473) 4.5.1
# start the command batch
batch start
defaults nosave
board_name BETAFPVG473
set craft_name = WHOOP
set dshot_bidir = ON
set motor_poles = 12
# end the command batch
batch end
save"""

FLASH_INFO_REPLY = (
    "Flash sectors=512, sectorSize=4096, pagesPerSector=16, "
    "pageSize=256, totalSize=2097152\n"
    "FlashFS: offset = 131072, usedSize = 131072"
)


# Betaflight's `get` matches on substring and prints every variable whose name
# contains the query, alphabetically. These entries deliberately include names
# that collide, so tests exercise the real behaviour.
DEFAULT_VARIABLES = {
    "craft_name": "WHOOP",
    "dshot_bidir": "ON",
    "motor_poles": "12",
    "motor_pwm_protocol": "DSHOT300",
    "dyn_idle_min_rpm": "30",
    "osd_craft_name_pos": "395",
    "osd_vtx_channel_pos": "18522",
    "serialrx_provider": "CRSF",
    "vtx_channel": "3",
    "vtx_band": "5",
}


def default_responses():
    """Canned replies keyed by the exact command text."""
    return {
        "status": STATUS_REPLY,
        "version": VERSION_REPLY,
        "diff all": DIFF_ALL_REPLY,
        "diff": DIFF_ALL_REPLY,
        "flash_info": FLASH_INFO_REPLY,
        "flash_erase": "Erasing, please wait ... Done.",
        "save": "Saving...",
        "exit noreboot": "",
        "defaults nosave": "Resetting to defaults",
    }


class FakeFlightController:
    """A scriptable fake serial device speaking the Betaflight CLI.

    Attributes:
        commands: Every command line received, in order. Includes the closing
            `exit noreboot` or `save`, so exit discipline can be asserted.
        written: Raw byte payloads received, including the bare `#` handshake.
    """

    def __init__(self, responses=None, error_on=(), raise_on=None,
                 prompt_on_handshake=True, variables=None):
        self.variables = dict(DEFAULT_VARIABLES)
        if variables:
            self.variables.update(variables)
        self.responses = default_responses()
        if responses:
            self.responses.update(responses)
        self.error_on = set(error_on)
        # {command: exception} -- simulates the link dying mid-command, e.g. a
        # yanked USB cable or a Ctrl-C. The command is recorded before the
        # exception fires, so cleanup behaviour stays observable.
        self.raise_on = dict(raise_on or {})
        self.prompt_on_handshake = prompt_on_handshake

        self.commands = []
        self.written = []
        self.closed = False
        self.dtr = False
        self._out = bytearray()

    # factory

    def as_factory(self):
        """Return a (port, baud, timeout) -> self callable for CliSession."""
        def factory(port, baud, timeout):
            self.port = port
            self.baud = baud
            self.timeout = timeout
            return self
        return factory

    # serial.Serial surface

    def write(self, data):
        self.written.append(bytes(data))

        if data == b"#":
            # Bare '#' handshake: the board answers with a prompt only.
            if self.prompt_on_handshake:
                self._out += PROMPT
            return len(data)

        text = data.decode("utf-8", "replace").strip()
        if text:
            self.commands.append(text)
            if text in self.raise_on:
                raise self.raise_on[text]
            body = self._reply_for(text)
            echoed = text.encode("utf-8") + b"\r\n"
            self._out += echoed + body.encode("utf-8") + PROMPT
        return len(data)

    def read(self, size=1):
        if not self._out:
            return b""
        chunk = bytes(self._out[:size])
        del self._out[:size]
        return chunk

    def flush(self):
        pass

    def reset_input_buffer(self):
        self._out.clear()

    def reset_output_buffer(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    # behaviour

    def _reply_for(self, command):
        if command in self.error_on:
            return "Invalid name"
        if command in self.responses:
            return self.responses[command]

        head = command.split(None, 1)[0].lower()
        if head == "get":
            name = command.split(None, 1)[1].strip()
            # Substring match, as the firmware does. Real boards list by their
            # internal table order, which put `osd_craft_name_pos` ahead of
            # `craft_name`. Partial matches are emitted first here so a naive
            # first-line parser fails in tests the same way it fails on metal.
            matches = sorted(
                (n for n in self.variables if name.lower() in n.lower()),
                key=lambda n: (n.lower() == name.lower(), n.lower()),
            )
            if matches:
                return "\n\n".join(
                    f"{n} = {self.variables[n]}\nDefault value: 0" for n in matches
                )
            return "Invalid name"
        if head == "set":
            remainder = command.split(None, 1)[1]
            name, _, value = remainder.partition("=")
            return f"{name.strip()} set to {value.strip()}"
        if head in ("motor", "map", "feature", "batch", "profile"):
            return ""
        return ""


class FakeMspDevice:
    """Serial stand-in speaking MSP v1, for `bf_msp.py`.

    MSP is a separate protocol from the CLI: binary framed, not line based, so
    it needs its own fake. Frames are `$M<` for a request and `$M>` for a
    reply, with an XOR checksum over the length, the code, and the payload.

    Usage:
        device = FakeMspDevice({101: struct.pack("<H", 1500)})
        payload, error = msp_request(device, 101)
    """

    def __init__(self, responses=None, corrupt_checksum=False, send_error=False):
        self.responses = dict(responses or {})
        self.corrupt_checksum = corrupt_checksum
        self.send_error = send_error
        self.requested = []
        self._out = bytearray()
        self.closed = False
        self.dtr = False

    @staticmethod
    def frame(code, payload, corrupt=False):
        """Build a `$M>` reply frame with a correct (or deliberately bad) checksum."""
        length = len(payload)
        checksum = length ^ code
        for byte in payload:
            checksum ^= byte
        if corrupt:
            checksum ^= 0xFF
        return b"$M>" + bytes([length, code]) + bytes(payload) + bytes([checksum])

    def write(self, data):
        # A request is $M< + length + code + checksum.
        if data[:3] == b"$M<" and len(data) >= 6:
            code = data[4]
            self.requested.append(code)
            if self.send_error:
                self._out += b"$M!" + bytes([0, code, code])
            else:
                payload = self.responses.get(code, b"")
                self._out += self.frame(code, payload, self.corrupt_checksum)
        return len(data)

    def read(self, size=1):
        if not self._out:
            return b""
        chunk = bytes(self._out[:size])
        del self._out[:size]
        return chunk

    def flush(self):
        pass

    def reset_input_buffer(self):
        self._out.clear()

    def close(self):
        self.closed = True
