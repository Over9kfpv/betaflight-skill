#!/usr/bin/env python3
"""Betaflight CLI variable registry and response validation.

The variable table is generated from a live flight controller by
`scripts/dump_vars.py` and lives in `scripts/bf_vars_table.py`. It carries every
setting the reference firmware exposes, along with each one's kind and
constraint, so a value can be checked as well as a name. Regenerate it after a
firmware update or to match a different board.

Betaflight answers an unrecognised `get`/`set` with an error line and carries
on. Nothing in the toolchain checked for that, so a typo in a tuning preset
silently failed to apply. This module catches both classes of mistake:

  * `validate()`  -- reject an unknown variable name before it is sent.
  * `check_response()` -- raise when the board replies with an error.

Names follow Betaflight 4.4 and later. Where a name changed between firmware
versions, the superseded spelling is listed in RENAMED_VARS with its
replacement so the error message can point at the fix.
"""

from bf_vars_table import FIRMWARE, GENERATED, VARIABLES

# Every setting the reference firmware exposes, read from a live board by
# scripts/dump_vars.py rather than maintained by hand.
KNOWN_VARS = frozenset(VARIABLES)

# Superseded or invented names -> what to use instead.
RENAMED_VARS = {
    "dynamic_idle_min_rpm": "dyn_idle_min_rpm",
    "idle_min_rpm": "dyn_idle_min_rpm",
    "crsf_use_painless_telemetry": "crsf_use_negotiated_baud",
    "rx_serial_protocol": "serialrx_provider",
    "telemetry_disabled": "the `feature TELEMETRY` command (not a variable)",
    "map": "the bare `map AETR1234` command (not a variable)",
    "dterm_lowpass_hz": "dterm_lpf1_static_hz",
    "gyro_lowpass_hz": "gyro_lpf1_static_hz",
    "d_setpoint_weight": "f_pitch / f_roll / f_yaw",
}

# CLI commands that are not variables and must never be wrapped in get/set.
BARE_COMMANDS = {
    "status", "version", "diff", "dump", "defaults", "save", "exit",
    "map", "motor", "feature", "beeper", "resource", "mixer", "tasks",
    "flash_info", "flash_erase", "flash_read", "flash_write", "msc",
    "bl", "dfu", "mcu_id", "serial", "aux", "adjrange", "rxrange",
    "led", "color", "mode_color", "play_sound", "profile", "rateprofile",
    "bind_rx", "vtx", "vtxtable", "escprog", "gyroregisters", "batch",
}

# Substrings Betaflight emits when it rejects input.
ERROR_MARKERS = (
    "Invalid name",
    "Invalid value",
    "Unknown command",
    "Parse error",
    "ERROR:",
)


class UnknownVariableError(ValueError):
    """Raised when a command targets a variable Betaflight will not accept."""


class CliResponseError(RuntimeError):
    """Raised when the Flight Controller rejects a command."""


def variable_name(command):
    """Return the variable a `get`/`set` command targets, or None.

    >>> variable_name("set dyn_idle_min_rpm = 30")
    'dyn_idle_min_rpm'
    >>> variable_name("status") is None
    True
    """
    parts = command.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() not in ("get", "set"):
        return None
    remainder = parts[1]
    return remainder.split("=", 1)[0].strip()


def validate(commands):
    """Check commands against the registry, returning a list of problems.

    An empty list means every command looks sendable.
    """
    problems = []
    for cmd in commands:
        name = variable_name(cmd)
        if name is None:
            continue
        if name in RENAMED_VARS:
            problems.append(
                f"`{cmd}` uses '{name}', which this firmware does not accept. "
                f"Use {RENAMED_VARS[name]}."
            )
        elif name in BARE_COMMANDS:
            problems.append(
                f"`{cmd}` wraps '{name}' in get/set, but '{name}' is a bare CLI "
                f"command. Send `{name}` on its own instead."
            )
        elif name not in KNOWN_VARS:
            problems.append(
                f"`{cmd}` targets unknown variable '{name}'. It is not in the "
                f"reference firmware ({FIRMWARE}). If your board is newer, "
                f"regenerate the table with `python scripts/dump_vars.py`."
            )
        else:
            problem = value_problem(cmd, name)
            if problem:
                problems.append(problem)
    return problems


def value_problem(command, name):
    """Check a `set` command's value against the variable's constraint."""
    if "=" not in command or command.strip().split(None, 1)[0].lower() != "set":
        return None

    value = command.split("=", 1)[1].strip()
    if not value:
        return None

    entry = VARIABLES.get(name, {})
    kind = entry.get("kind")

    if kind == "enum":
        allowed = entry.get("values", [])
        if value.upper() not in {v.upper() for v in allowed}:
            return (f"`{command}` sets '{name}' to '{value}', which is not one "
                    f"of: {', '.join(allowed)}.")
    elif kind == "int":
        try:
            number = int(value)
        except ValueError:
            # Some integer settings accept a fraction, e.g. blackbox_sample_rate.
            return None
        low, high = entry.get("min"), entry.get("max")
        if low is not None and not low <= number <= high:
            return (f"`{command}` sets '{name}' to {number}, outside the "
                    f"allowed range {low} to {high}.")
    elif kind == "string":
        longest = entry.get("maxlen")
        if longest is not None and len(value) > longest:
            return (f"`{command}` sets '{name}' to {len(value)} characters, "
                    f"over the {longest} character limit.")
    return None


def assert_valid(commands):
    """Raise UnknownVariableError if any command would be rejected."""
    problems = validate(commands)
    if problems:
        raise UnknownVariableError(
            "Refusing to send commands the flight controller will reject:\n  - "
            + "\n  - ".join(problems)
        )


def parse_get(output, name):
    """Return the value of `name` from a `get` reply, or None.

    Betaflight's `get` matches on substring and prints every variable whose
    name contains the query, so `get craft_name` also returns
    `osd_craft_name_pos` -- and returns it first, because the list is
    alphabetical. Taking the first line with an '=' therefore reads the wrong
    variable. This matches the name exactly.
    """
    target = name.strip().lower()
    for line in output.splitlines():
        if "=" not in line:
            continue
        left, _, right = line.partition("=")
        if left.strip().lower() == target:
            return right.strip()
    return None


def check_response(command, output):
    """Raise CliResponseError when the FC's reply signals a rejection."""
    for marker in ERROR_MARKERS:
        if marker in output:
            line = next(
                (l.strip() for l in output.splitlines() if marker in l), output.strip()
            )
            raise CliResponseError(f"Flight controller rejected `{command}`: {line}")


def check_responses(results):
    """Apply check_response across a {command: output} mapping."""
    for command, output in results.items():
        check_response(command, output)
