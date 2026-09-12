# Betaflight agent skill

An agent skill for talking to a Betaflight flight controller over USB serial:
detect the board, run CLI commands, read and write settings with validation,
back up and restore the configuration, read live telemetry, and pull, decode
and analyze blackbox flight logs.

Pure Python with one dependency, pyserial. No Configurator, no MCP server, no
build step. Works on Windows, macOS and Linux.

## Install

With the GitHub CLI, version 2.90 or later:

```bash
gh skill install HansF/betaflight-skill betaflight --agent claude-code
```

Other hosts use the same command with a different `--agent`: `github-copilot`,
`cursor`, `codex`, `gemini-cli`, and others. Add `--scope user` to install it
once for every project instead of only the current repository.

Pin a version for reproducibility:

```bash
gh skill install HansF/betaflight-skill betaflight --pin v1.0.0
```

Then install the runtime dependency:

```bash
pip install pyserial
```

`numpy` is optional. It only speeds up the FFT in the log analyzer, which falls
back to a pure-Python implementation.

## What it does

Ask your agent something like "check if my quad is connected", "what is my
craft name", "pull the blackbox logs and tell me if a motor is dragging", or
"set dynamic idle to 30 and verify it stuck". The skill covers:

| Script | Purpose |
| --- | --- |
| `fc_connect.py` | Detect the board, verify the CLI link |
| `bf_cli.py` | Run arbitrary CLI commands in one safe session |
| `bf_params.py` | Read and write settings by name, validated and verified |
| `bf_msp.py` | Live telemetry and RC channels over MSP v1 |
| `blackbox.py` | Flash info, mass storage, copy, decode, analyze, erase |
| `analyze_log.py` | Motor balance and gyro noise from a decoded CSV |
| `backup_restore.py` | Capture and replay a full `diff all` configuration |
| `dump_vars.py` | Regenerate the variable table from your own firmware |
| `fc_serial.py` | Port discovery, CLI session, MSP framing (library) |
| `bf_vars.py` | Variable validation and reply checking (library) |
| `fake_fc.py` | In-memory board, so everything is testable with no hardware |

## The design constraint worth knowing

A Betaflight board in CLI mode answers nothing else, not MSP and not a new CLI
connection, until the USB cable is physically replugged. Every session in this
skill runs through `CliSession`, which sends `exit noreboot` or `save` from a
`finally` block, so the board is never left locked even when something raises
mid-session.

That is the whole reason this exists as a library rather than a set of
one-liners piping text into a serial port.

## Safety

Commands that touch motors, DShot, arming or the mixer can spin a motor.
**Remove all propellers** before running one. Take a configuration backup
before a tuning session; a `diff all` capture is the only reliable way back.

## Tests

30 tests, all against an in-memory fake board. No flight controller needed.

```bash
python -m unittest discover -s tests -t .
```

## Regenerating the variable table

`skills/betaflight/scripts/bf_vars_table.py` is generated from a live board and
validates every `get` and `set` before it is sent. The shipped copy came from
Betaflight 2026.6.0-alpha on an STM32G473. If your firmware has settings it does
not know about, regenerate it from your own board:

```bash
python skills/betaflight/scripts/dump_vars.py
```

## License

MIT. See [LICENSE](LICENSE).
