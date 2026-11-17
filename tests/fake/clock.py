"""
The fake clock shared by the test containers.

libfaketime reads the current offset from <dir>/faketime ("+<seconds>s"). Python
does not see faked file times, so tests/fake/site/sitecustomize.py shifts them,
using <dir>/history: one line "<real epoch> <offset>" per change. A file
written before a jump keeps the time it was written at.

Usage:
  clock.py <dir> start <ISO time>     set the fake time (e.g. 2027-03-29T21:59:00Z)
  clock.py <dir> to <ISO time>        move the fake time forward to that moment
  clock.py <dir> jump <seconds>       move the fake time forward
  clock.py <dir> now                  print the fake time
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def offset(clock):
    text = (Path(clock) / "faketime").read_text().strip()
    return float(text.rstrip("s"))


def now(clock):
    return datetime.fromtimestamp(time.time() + offset(clock), timezone.utc)


def set_offset(clock, off, append):
    clock = Path(clock)
    clock.mkdir(parents=True, exist_ok=True)
    real = time.time()
    with open(clock / "history", "a" if append else "w") as f:
        f.write(f"{real:.3f} {off:.0f}\n")
    tmp = clock / "faketime.tmp"
    tmp.write_text(f"+{off:.0f}s\n")
    tmp.replace(clock / "faketime")


def main(clock, cmd, arg=None):
    if cmd == "start":
        when = datetime.fromisoformat(arg.replace("Z", "+00:00"))
        set_offset(clock, when.timestamp() - time.time(), append=False)
    elif cmd == "to":
        when = datetime.fromisoformat(arg.replace("Z", "+00:00"))
        if when <= now(clock):
            sys.exit(f"{arg} is not ahead of the fake clock ({now(clock).isoformat(timespec='seconds')})")
        set_offset(clock, when.timestamp() - time.time(), append=True)
    elif cmd == "jump":
        set_offset(clock, offset(clock) + float(arg), append=True)
    print(now(clock).isoformat(timespec="seconds"))


if __name__ == "__main__":
    main(*sys.argv[1:])
