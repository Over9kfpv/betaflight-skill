"""Put the skill's scripts directory on sys.path.

The scripts import each other flatly (`from fc_serial import CliSession`),
because that is what works when an agent runs one as
`python scripts/bf_cli.py` from any directory. The tests import them the
same way, so the scripts directory has to be importable.

Run the suite from the repository root:

    python -m unittest discover -s tests -t .
"""
import os
import sys

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "betaflight", "scripts",
)

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
