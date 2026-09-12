"""Metrics from a decoded blackbox CSV."""
import math
import os
import tempfile
import unittest

import analyze_log


def write_log(path, rows=512, erpm=True):
    """Synthesize a decoded CSV: four motors, three gyro axes, 8 kHz."""
    header = ["time (us)", "motor[0]", "motor[1]", "motor[2]", "motor[3]"]
    if erpm:
        header += [f"eRPM[{i}]" for i in range(4)]
    header += [f"gyroADC[{i}]" for i in range(3)]

    lines = [", ".join(header)]
    for index in range(rows):
        values = [index * 125, 1200, 1200, 1200, 1200]  # 125 us = 8 kHz
        if erpm:
            # Motor 3 turns 20% slower for the same command.
            values += [6000, 6000, 4800, 6000]
        wave = math.sin(2 * math.pi * 200 * index / 8000) * 5
        values += [wave, wave, wave]
        lines.append(", ".join(str(v) for v in values))

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


class Metrics(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def log(self, **kwargs):
        return write_log(os.path.join(self.dir.name, "log.csv"), **kwargs)

    def test_sample_rate_is_derived_from_the_time_column(self):
        report = analyze_log.analyze(self.log())
        self.assertEqual(round(report["sample_rate_hz"]), 8000)

    def test_a_dragging_motor_shows_up_as_deviation(self):
        report = analyze_log.analyze(self.log())
        deviation = {row["motor"]: row["deviation_pct"] for row in report["motors"]}
        self.assertLess(deviation[3], -5)
        for motor in (1, 2, 4):
            self.assertLess(abs(deviation[motor]), 10)

    def test_the_warning_names_the_dragging_motor(self):
        markdown = analyze_log.format_markdown(analyze_log.analyze(self.log()))
        self.assertIn("Motor 3 deviates", markdown)

    def test_missing_erpm_reads_as_unavailable_not_zero(self):
        report = analyze_log.analyze(self.log(erpm=False))
        self.assertTrue(all(row["ratio"] is None for row in report["motors"]))
        self.assertIn("n/a", analyze_log.format_markdown(report))

    def test_gyro_noise_is_reported_per_axis(self):
        report = analyze_log.analyze(self.log())
        self.assertEqual(set(report["gyro_noise"]), {"roll", "pitch", "yaw"})
        for value in report["gyro_noise"].values():
            self.assertGreater(value, 0)


if __name__ == "__main__":
    unittest.main()
