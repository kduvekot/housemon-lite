"""
Per-day figures for housemon-lite, by local day (midnight in --tz, e.g.
Europe/Amsterdam, summer and winter time included).

P1: the meter's counters only go up, so a day needs just two readings: the
first valid telegram at or after local midnight, and the last one before the
next midnight. Only the first or last few KB of one hourly file are read.

S0: the node's pulse counters restart at 0 when it loses power and wrap at
65536, so they cannot simply be subtracted. Pulses are added up line by line
(S0Totals), which handles both. A completed day never changes, so it is
computed once and kept in memory.
"""

import re
from datetime import date, datetime, timedelta, timezone

P1_TELEGRAM = re.compile(rb"/[^!]*!([0-9A-Fa-f]{4})\r?\n")
P1_REGISTER = re.compile(rb"^1-0:([12]\.8\.[12])\((\d+\.\d+)\*kWh\)", re.M)
P1_GAS = re.compile(rb"^0-\d:24\.2\.1\(\d{12}[SW]\)\((\d+\.\d+)\*m3\)", re.M)
S0_LINE = re.compile(r"^L (\S+)Z \S+ OK (\d+)((?: \d+)*)\s*$")

WRAP = 65536
EDGE = 600      # seconds: data within 10 min of a day's start/end counts as complete


# --- time ---------------------------------------------------------------------

def day_bounds(day, tz):
    """(start, end) of a local day, as UTC datetimes."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_today(tz):
    return datetime.now(tz).date()


# --- P1 -----------------------------------------------------------------------

def crc16(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def p1_readings(telegram):
    """Meter counters from one telegram (bytes), or None if its CRC is wrong."""
    bang = telegram.rindex(b"!")
    if crc16(telegram[:bang + 1]) != int(telegram[bang + 1:bang + 5], 16):
        return None
    r = {k.decode(): float(v) for k, v in P1_REGISTER.findall(telegram)}
    gas = P1_GAS.search(telegram)
    if gas:
        r["gas"] = float(gas.group(1))
    return r or None


def p1_file(p1dir, hour):
    return p1dir / hour.strftime("%Y") / hour.strftime("%Y%m%d") / (hour.strftime("%H") + ".txt")


def p1_first(p1dir, start, end):
    """First valid readings in [start, end): (readings, utc hour) or (None, None)."""
    hour = start
    while hour < end:
        try:
            with open(p1_file(p1dir, hour), "rb") as f:
                data = f.read(16384)
        except OSError:
            data = b""
        for m in P1_TELEGRAM.finditer(data):
            r = p1_readings(m.group(0))
            if r:
                return r, hour
        hour += timedelta(hours=1)
    return None, None


def p1_last(p1dir, start, end):
    """Last valid readings in [start, end): (readings, utc hour) or (None, None)."""
    hour = end - timedelta(hours=1)
    while hour >= start:
        try:
            with open(p1_file(p1dir, hour), "rb") as f:
                size = f.seek(0, 2)
                f.seek(max(0, size - 16384))
                data = f.read()
        except OSError:
            data = b""
        for m in reversed(list(P1_TELEGRAM.finditer(data))):
            r = p1_readings(m.group(0))
            if r:
                return r, hour
        hour -= timedelta(hours=1)
    return None, None


# --- S0 -----------------------------------------------------------------------

class S0Totals:
    """Adds up S0 pulses per node and channel, across counter resets and wraps."""

    def __init__(self):
        self.prev = {}      # node -> list of 8 counters
        self.pulses = {}    # node -> {channel: pulses}
        self.first = None   # time of the first and last line counted
        self.last = None

    def add(self, when, node, values):
        counts = [values[2 * i] | values[2 * i + 1] << 8 for i in range(0, 16, 2)]
        prev = self.prev.get(node)
        tot = self.pulses.setdefault(node, {ch: 0 for ch in range(1, 9)})
        if prev:
            for ch, (old, new) in enumerate(zip(prev, counts), start=1):
                if new >= old:
                    tot[ch] += new - old
                elif old > WRAP - 5000 and new < 5000:
                    tot[ch] += new + WRAP - old     # counter wrapped
                else:
                    tot[ch] += new                  # node restarted: counts from 0
        self.prev[node] = counts
        self.first = self.first or when
        self.last = when

    def as_json(self):
        return {str(n): {str(c): p for c, p in chs.items()} for n, chs in self.pulses.items()}


def parse_s0(line):
    """(utc datetime, node, 32 data bytes) or None."""
    m = S0_LINE.match(line)
    if not m:
        return None
    values = [int(x) for x in m.group(3).split()]
    if len(values) < 32:
        return None
    when = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
    return when, int(m.group(2)) & 0x1F, values[:32]


def s0_scan(s0dir, start, end, totals=None):
    """Add all S0 lines with start <= time < end to `totals` (new if None)."""
    totals = totals or S0Totals()
    day = start.date()
    while day <= end.date():
        path = s0dir / day.strftime("%Y") / (day.strftime("%Y%m%d") + ".txt")
        try:
            with open(path, encoding="ascii", errors="replace") as f:
                for line in f:
                    p = parse_s0(line)
                    if p and start <= p[0] < end:
                        totals.add(*p)
        except OSError:
            pass
        day += timedelta(days=1)
    return totals


# --- one day ---------------------------------------------------------------------

def summarize_day(day, tz, s0dir, p1dir):
    """Figures for one completed local day."""
    start, end = day_bounds(day, tz)
    first, fh = p1_first(p1dir, start, end)
    last, lh = p1_last(p1dir, start, end)
    s0 = s0_scan(s0dir, start, end)
    return {
        "date": day.isoformat(),
        "p1": {"first": first, "last": last,
               "partial": first is None or fh != start or lh != end - timedelta(hours=1)},
        "s0": {"pulses": s0.as_json(),
               "partial": s0.first is None or (s0.first - start).total_seconds() > EDGE
                          or (end - s0.last).total_seconds() > EDGE},
    }
