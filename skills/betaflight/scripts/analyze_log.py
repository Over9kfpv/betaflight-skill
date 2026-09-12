#!/usr/bin/env python3
"""Compute motor balance and gyro noise from a decoded blackbox log.

Input is the CSV that blackbox_decode produces:

    python scripts/blackbox.py decode logs/btfl_001.bbl
    python scripts/analyze_log.py logs/btfl_001.01.csv

Metrics:
  * Motor balance -- mean eRPM divided by mean motor output, per motor. A motor
    turning slower than its siblings for the same command is dragging: a worn
    bearing, a bent shaft, or a cold solder joint.
  * Gyro noise floor -- mean spectral magnitude above 100 Hz, per axis. Below
    that, craft motion dominates; above it, what remains is frame resonance and
    electrical motor noise.

numpy is used for the FFT when it is installed and a pure-Python radix-2 FFT is
used otherwise, so the skill's pyserial-only install stays valid.
"""
import argparse
import cmath
import csv
import os
import sys

NOISE_FLOOR_START_HZ = 100.0
DEFAULT_WINDOW = 4096

try:
    import numpy as _np
except ImportError:
    _np = None


# -- loading ---------------------------------------------------------------

def load_csv(path):
    """Read a decoded Blackbox CSV into {column: [float, ...]}.

    Column headers are padded with spaces by blackbox_decode, so they are
    stripped. Non-numeric cells become None rather than aborting the parse.
    """
    columns = {}
    with open(path, newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.reader(handle)
        try:
            header = [name.strip() for name in next(reader)]
        except StopIteration:
            raise ValueError(f"{path} is empty")

        for name in header:
            columns[name] = []

        for row in reader:
            for name, cell in zip(header, row):
                cell = cell.strip()
                try:
                    columns[name].append(float(cell))
                except ValueError:
                    columns[name].append(None)

    return columns


def _clean(values):
    return [v for v in values if v is not None]


def _mean(values):
    values = _clean(values)
    return sum(values) / len(values) if values else 0.0


def sample_rate_hz(columns, default=8000.0):
    """Derive the logging rate from the time column, in Hz."""
    time_key = next((k for k in columns if k.lower().startswith("time")), None)
    if not time_key:
        return default

    times = _clean(columns[time_key])[:2000]
    if len(times) < 2:
        return default

    deltas = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    if not deltas:
        return default

    median = deltas[len(deltas) // 2]
    return 1_000_000.0 / median if median else default


# -- motor balance ---------------------------------------------------------

def motor_balance(columns, motor_count=4):
    """Per-motor mean eRPM over mean output, plus deviation from the fleet mean.

    Returns a list of dicts. `ratio` is None when the log has no eRPM data,
    which is the case whenever bidirectional DShot was off.
    """
    rows = []
    for index in range(motor_count):
        motor_key = _find(columns, f"motor[{index}]")
        erpm_key = _find(columns, f"eRPM[{index}]")

        mean_motor = _mean(columns[motor_key]) if motor_key else 0.0
        mean_erpm = _mean(columns[erpm_key]) if erpm_key else None

        ratio = None
        if mean_erpm is not None and mean_motor:
            ratio = mean_erpm / mean_motor

        rows.append({
            "motor": index + 1,
            "mean_motor": mean_motor,
            "mean_erpm": mean_erpm,
            "ratio": ratio,
            "deviation_pct": None,
        })

    ratios = [r["ratio"] for r in rows if r["ratio"]]
    if ratios:
        fleet_mean = sum(ratios) / len(ratios)
        for row in rows:
            if row["ratio"] and fleet_mean:
                row["deviation_pct"] = (row["ratio"] - fleet_mean) / fleet_mean * 100.0

    return rows


def _find(columns, wanted):
    """Match a column name case-insensitively, ignoring surrounding spaces."""
    wanted_l = wanted.lower()
    for name in columns:
        if name.lower() == wanted_l:
            return name
    return None


# -- gyro noise ------------------------------------------------------------

def _fft(values):
    """Iterative radix-2 Cooley-Tukey FFT. Length must be a power of two."""
    n = len(values)
    if n & (n - 1):
        raise ValueError("FFT length must be a power of two")

    data = [complex(v) for v in values]

    # Bit-reversal permutation.
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            data[i], data[j] = data[j], data[i]

    length = 2
    while length <= n:
        step = cmath.exp(-2j * cmath.pi / length)
        for start in range(0, n, length):
            w = 1 + 0j
            half = length // 2
            for k in range(start, start + half):
                even = data[k]
                odd = data[k + half] * w
                data[k] = even + odd
                data[k + half] = even - odd
                w *= step
        length <<= 1

    return data


def _magnitudes(samples):
    """One-sided magnitude spectrum, normalised by window length."""
    n = len(samples)
    # Hann window suppresses spectral leakage from the non-periodic window.
    windowed = [v * (0.5 - 0.5 * cmath.cos(2 * cmath.pi * i / n).real)
                for i, v in enumerate(samples)]

    if _np is not None:
        spectrum = _np.fft.rfft(_np.asarray(windowed))
        return [abs(c) * 2.0 / n for c in spectrum]

    spectrum = _fft(windowed)
    return [abs(c) * 2.0 / n for c in spectrum[: n // 2 + 1]]


def _largest_power_of_two(value):
    power = 1
    while power * 2 <= value:
        power *= 2
    return power


def gyro_noise_floor(columns, rate_hz, window=DEFAULT_WINDOW,
                     start_hz=NOISE_FLOOR_START_HZ):
    """Mean spectral magnitude above `start_hz` for each gyro axis.

    Returns {axis_name: magnitude}. Units match the log's gyro column, which
    blackbox_decode writes in degrees per second.
    """
    results = {}
    for index, axis in enumerate(("roll", "pitch", "yaw")):
        key = _find(columns, f"gyroADC[{index}]")
        if key is None:
            continue

        samples = _clean(columns[key])
        usable = _largest_power_of_two(min(len(samples), window))
        if usable < 64:
            continue

        # Take the window from the middle of the log, away from arm and disarm.
        start = max(0, (len(samples) - usable) // 2)
        magnitudes = _magnitudes(samples[start:start + usable])

        bin_hz = rate_hz / usable
        first_bin = int(start_hz / bin_hz) if bin_hz else 0
        band = magnitudes[first_bin:]
        if band:
            results[axis] = sum(band) / len(band)

    return results


# -- reporting -------------------------------------------------------------

def analyze(path, window=DEFAULT_WINDOW):
    """Run both analyses over one decoded CSV."""
    columns = load_csv(path)
    rate = sample_rate_hz(columns)
    return {
        "path": path,
        "sample_rate_hz": rate,
        "motors": motor_balance(columns),
        "gyro_noise": gyro_noise_floor(columns, rate, window=window),
        "backend": "numpy" if _np is not None else "pure-python",
    }


MOTOR_POSITIONS = {1: "Rear Right", 2: "Front Right", 3: "Rear Left", 4: "Front Left"}


def _fmt_noise(value):
    """Format a noise magnitude without collapsing small values to 0.00.

    A very quiet axis can sit well below 0.01 deg/s. Printing `0.00` there is
    indistinguishable from having no data at all, so significant figures are
    used instead of fixed decimals.
    """
    if value == 0:
        return "0"
    if value >= 0.01:
        return f"{value:.2f}"
    return f"{value:.3g}"


def format_markdown(report):
    """Render the report as the Markdown block used in content/log entries."""
    lines = ["- **Motor Balance Ratios** (`mean eRPM / mean motor output`):"]

    have_erpm = any(row["ratio"] for row in report["motors"])
    for row in report["motors"]:
        position = MOTOR_POSITIONS.get(row["motor"], "")
        label = f"Motor {row['motor']} ({position})".ljust(24)
        if row["ratio"] is None:
            lines.append(f"  - {label}: `n/a` (no eRPM data; enable bidirectional DShot)")
        else:
            deviation = row["deviation_pct"] or 0.0
            lines.append(
                f"  - {label}: `{row['ratio']:.2f}` ({deviation:+.1f}% vs fleet mean)"
            )

    if have_erpm:
        worst = max(
            (r for r in report["motors"] if r["deviation_pct"] is not None),
            key=lambda r: abs(r["deviation_pct"]),
            default=None,
        )
        if worst and abs(worst["deviation_pct"]) >= 5.0:
            lines.append(
                f"  - ⚠️ Motor {worst['motor']} deviates "
                f"{worst['deviation_pct']:+.1f}%. Inspect bearings, shaft, and solder joints."
            )

    lines.append("- **Gyro Noise Floor** (mean magnitude above 100 Hz):")
    if report["gyro_noise"]:
        for axis, value in report["gyro_noise"].items():
            lines.append(f"  - {axis.capitalize():<6}: `{_fmt_noise(value)}` deg/s")
    else:
        lines.append("  - `n/a` (no gyroADC columns in this log)")

    lines.append(f"- **Log sample rate**: `{report['sample_rate_hz']:.0f}` Hz")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze a decoded Blackbox CSV for motor balance and gyro noise."
    )
    parser.add_argument("csv_file", help="Decoded .csv produced by blackbox_decode")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help=f"FFT window in samples (default: {DEFAULT_WINDOW})")
    parser.add_argument("--markdown", action="store_true",
                        help="Emit the Markdown block used in flight log entries")
    args = parser.parse_args()

    if not os.path.exists(args.csv_file):
        print(f"ERROR: File not found: {args.csv_file}")
        sys.exit(1)

    report = analyze(args.csv_file, window=args.window)

    if args.markdown:
        print(format_markdown(report))
        return

    print_report(report)


def print_report(report):
    """Render the report as a plain-text table."""
    print(f"Log         : {report['path']}")
    print(f"Sample rate : {report['sample_rate_hz']:.0f} Hz")
    print(f"FFT backend : {report['backend']}")
    print()
    print("Motor balance")
    print(f"  {'Motor':<8}{'mean out':>10}{'mean eRPM':>12}{'ratio':>9}{'dev':>9}")
    for row in report["motors"]:
        ratio = f"{row['ratio']:.2f}" if row["ratio"] is not None else "n/a"
        erpm = f"{row['mean_erpm']:.0f}" if row["mean_erpm"] is not None else "n/a"
        dev = f"{row['deviation_pct']:+.1f}%" if row["deviation_pct"] is not None else "-"
        print(f"  {row['motor']:<8}{row['mean_motor']:>10.0f}{erpm:>12}{ratio:>9}{dev:>9}")

    print()
    print("Gyro noise floor (mean magnitude above 100 Hz)")
    if report["gyro_noise"]:
        for axis, value in report["gyro_noise"].items():
            print(f"  {axis:<8}{_fmt_noise(value):>10} deg/s")
    else:
        print("  no gyroADC columns found")


if __name__ == "__main__":
    main()
