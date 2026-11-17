"""
Writes the made-up household's log files (see world.py) the way the loggers do,
as the (fake) clock runs: first everything from the start of the data up to
now, then every half second what is new. After a clock jump the gap is written
at once. When the history is written, <data dir>/ready appears.

Usage: feed.py <data dir>
Layouts as on the Pi: s0/YYYY/YYYYMMDD.txt and airco/YYYY/YYYYMMDD.csv per UTC
day, p1/YYYY/YYYYMMDD/HH.txt per UTC hour.
"""

import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import world


def append(files):
    for path, chunks in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as f:
            f.write(b"".join(chunks))


def write(energy, data, start, end):
    files = defaultdict(list)
    for t in world.s0_times(start, end):
        files[data / "s0" / f"{t:%Y}" / f"{t:%Y%m%d}.txt"].append(world.s0_line(energy, t).encode())
    for t in world.p1_times(start, end):
        files[data / "p1" / f"{t:%Y}" / f"{t:%Y%m%d}" / f"{t:%H}.txt"].append(world.p1_telegram(energy, t))
    for line in world.airco_lines(energy, start, end):
        t = datetime.strptime(line[:19], "%Y-%m-%dT%H:%M:%S")
        files[data / "airco" / f"{t:%Y}" / f"{t:%Y%m%d}.csv"].append(line.encode())
    append(files)


def main(data):
    data = Path(data)
    began = time.monotonic()
    energy = world.Energy()
    until = world.S0_START
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if now > world.MODEL_END:
            sys.exit(f"the clock ({now}) is past the end of the model ({world.MODEL_END})")
        if now > until:
            write(energy, data, until, now)
            until = now
        if not (data / "ready").exists():
            (data / "ready").write_text(f"{until.isoformat()}\n")
            print(f"history written up to {until} in {time.monotonic() - began:.0f} s", flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    main(sys.argv[1])
