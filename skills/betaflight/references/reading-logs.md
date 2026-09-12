# Reading a blackbox log

`scripts/analyze_log.py` computes two numbers from a decoded CSV. Both are
comparative. Neither has a universal pass mark, so read them against the other
motors, the other axes, and the same craft's past flights.

## Motor balance

For each motor: mean eRPM divided by mean motor output, then each motor's
deviation from the mean of all four.

Every motor is given the same command range by the mixer over a hover. If one
turns measurably slower for the same command, something is costing it torque.
In rough order of likelihood on a small quad:

- A slipping or damaged prop, which is free to check and worth ruling out first.
- A worn or dry bearing, usually audible as a rougher note from that corner.
- A cold or cracked solder joint at the ESC pad, which also shows up as
  intermittent desync.
- A damaged winding, which tends to come with visible heat after a flight.

The tool warns past 5% deviation. Smaller spreads are normal and vary with how
level the hover was.

The ratio reads `n/a` when the log contains no eRPM columns. That means
bidirectional DShot was off during the flight, so the board never received RPM
telemetry to record. Turn it on and refly:

```bash
python scripts/bf_params.py --get dshot_bidir motor_poles
python scripts/bf_params.py --set dshot_bidir=ON
```

`motor_poles` must match the motors or the eRPM figures are scaled wrong. Most
whoop motors have 12 poles.

## Gyro noise floor

Mean spectral magnitude above 100 Hz, per axis, from a Hann-windowed FFT taken
from the middle of the log, away from arming and disarming.

Below 100 Hz the spectrum is dominated by actual craft motion: stick inputs,
propwash, the natural response of the quad. Above it, what remains is frame
resonance and electrical noise from the motors. That is the part filters exist
to remove and the part that heats motors when it reaches them.

How to read it:

- **One axis much noisier than the others** points at a mechanical problem on
  that axis. A loose arm, a cracked frame, a motor bell rubbing.
- **All axes rising across flights** on the same craft usually means wear:
  bearings, or a frame that has taken hits.
- **A large drop after a filter change** confirms the change did something.
  This is the fastest way to tell a real improvement from a placebo.

Units are degrees per second, matching what blackbox_decode writes for the gyro
columns.

## The decoder's "75% of iterations missing" warning

Expected. The default `blackbox_sample_rate` of 1/4 logs one loop iteration in
four to make the flash last. The decoder counts the gaps and reports them as
missing data. Nothing is wrong.

Set `blackbox_sample_rate` to 1/1 for a short diagnostic flight when you need
full resolution, then set it back. At full rate a 2 MB flash holds well under a
minute of flight.

## Sample rate

The report prints the rate it derived from the log's time column. If it looks
implausible, say 30 Hz on a quad, the log is probably truncated or the CSV came
from something other than blackbox_decode.
