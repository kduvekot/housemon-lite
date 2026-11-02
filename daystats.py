"""
Per-day figures for housemon-lite, by local day (midnight in --tz, e.g.
Europe/Amsterdam, summer and winter time included).

P1: the meter's counters only go up, so a day needs just two readings: the
first valid telegram at or after local midnight, and the last one before the
next midnight. Only the first or last few KB of one hourly file are read.

S0: the node's pulse counters restart at 0 when it loses power and wrap at
65536, so they cannot simply be subtracted. Pulses are added up line by line
(S0Totals), which handles both. A completed day never changes, so it is
computed once and stored as a small JSON file (cached_day).

For days without P1 data, S0Totals also estimates what went to and from the
grid: house minus solar, added up per 30 seconds (positive = in, negative =
out). Checked against a real P1 meter over 4.7 hours: within 1.5 %. Shorter
intervals overcount (pulses are too coarse), longer ones let in and out
cancel each other.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone

P1_TELEGRAM = re.compile(rb"/[^!]*!([0-9A-Fa-f]{4})\r?\n")
P1_REGISTER = re.compile(rb"^1-0:([12]\.8\.[12])\((\d+\.\d+)\*kWh\)", re.M)
P1_GAS = re.compile(rb"^0-\d:24\.2\.1\(\d{12}[SW]\)\((\d+\.\d+)\*m3\)", re.M)
S0_LINE = re.compile(r"^L (\S+)Z \S+ OK (\d+)((?: \d+)*)\s*$")

WRAP = 65536
EDGE = 600      # seconds: data within 10 min of a day's start/end counts as complete
GRID_INTERVAL = 30   # seconds per step of the S0 grid estimate
SETTLE = timedelta(hours=2)   # a day's figures are final this long after it ended


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

RESTART_MAX = 100   # pulses: after a node restart its counters start again near 0
SILENCE = 30        # seconds without packets after which a drop means a restart


def is_stale(prev, counts, silence=0.0):
    """True if `counts` is an older packet than `prev`, judged on the data alone.

    Counters only go up, except when the node restarts (they start again at 0)
    or a counter wraps at 65536. A counter that went down but not to near 0,
    and not by a wrap, means this packet is older than the last one counted:
    e.g. a second receiver's copy that sorts in the wrong place because its
    clock differs slightly. Such a packet adds nothing and is skipped.
    """
    # The node restarts as a whole: all its counters start again at 0. One
    # counter that clearly did so (a big drop to near 0) marks the packet as a
    # restart, also for channels whose counters were still small. So does a
    # drop after a long silence: the node sends every few seconds, so it was
    # off; a stale copy from a second receiver comes within seconds.
    if silence > SILENCE:
        return False
    if any(new <= RESTART_MAX and old - new >= RESTART_MAX for old, new in zip(prev, counts)):
        return False
    for old, new in zip(prev, counts):
        wrapped = old > WRAP - 5000 and new < 5000
        if new < old and not wrapped:
            return True
    return False


class S0Totals:
    """Adds up S0 pulses per node and channel, across counter resets and wraps.

    `grid` (optional): {"node": 9, "house": [2], "solar": [3, 4]} -- then the
    pulses of house minus solar are also split into grid in and grid out.
    """

    def __init__(self, grid=None):
        self.grid = grid
        self.prev = {}      # node -> list of 8 counters
        self.prev_time = {} # node -> time of the last packet counted
        self.pulses = {}    # node -> {channel: pulses}
        self.first = None   # time of the first and last line counted
        self.last = None
        self.grid_in = 0    # pulses (house minus solar) per GRID_INTERVAL, summed
        self.grid_out = 0
        self.step_start = None
        self.step_net = 0

    def add(self, when, node, values):
        counts = [values[2 * i] | values[2 * i + 1] << 8 for i in range(0, 16, 2)]
        prev = self.prev.get(node)
        silence = (when - self.prev_time[node]).total_seconds() if node in self.prev_time else 0.0
        if prev and is_stale(prev, counts, silence):
            return None     # an older packet (e.g. from a second receiver): already counted
        tot = self.pulses.setdefault(node, {ch: 0 for ch in range(1, 9)})
        delta = {}
        if prev:
            for ch, (old, new) in enumerate(zip(prev, counts), start=1):
                if new >= old:
                    delta[ch] = new - old
                elif old > WRAP - 5000 and new < 5000:
                    delta[ch] = new + WRAP - old    # counter wrapped
                else:
                    delta[ch] = new                 # node restarted: counts from 0
                tot[ch] += delta[ch]
        self.prev[node] = counts
        self.prev_time[node] = when
        self.first = self.first or when
        self.last = when
        if self.grid and node == self.grid["node"] and delta:
            self.step_net += sum(delta[c] for c in self.grid["house"]) - sum(delta[c] for c in self.grid["solar"])
            if self.step_start is None:
                self.step_start = when
            elif (when - self.step_start).total_seconds() >= GRID_INTERVAL:
                self.close_step()
                self.step_start = when
        return delta        # pulses added per channel by this packet ({} for the first one)

    def close_step(self):
        if self.step_net > 0:
            self.grid_in += self.step_net
        else:
            self.grid_out -= self.step_net
        self.step_net = 0

    def as_json(self):
        return {str(n): {str(c): p for c, p in chs.items()} for n, chs in self.pulses.items()}

    def grid_json(self):
        """Estimated grid in/out in pulses, including the step still open."""
        if not self.grid:
            return None
        net = self.step_net
        return {"in": self.grid_in + max(net, 0), "out": self.grid_out + max(-net, 0)}


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


def s0_scan(s0dirs, start, end, totals):
    """Add all S0 lines with start <= time < end to `totals`.

    `s0dirs`: the logger's directory plus optional extra ones (e.g. a temporary
    backfill from another receiver); per UTC day their lines are merged by time.
    A packet received twice adds nothing the second time (same counters).
    """
    day = start.date()
    while day <= end.date():
        lines = []
        for s0dir in s0dirs:
            path = s0dir / day.strftime("%Y") / (day.strftime("%Y%m%d") + ".txt")
            try:
                with open(path, encoding="ascii", errors="replace") as f:
                    for line in f:
                        p = parse_s0(line)
                        if p and start <= p[0] < end:
                            lines.append(p)
            except OSError:
                pass
        if len(s0dirs) > 1:
            lines.sort(key=lambda p: p[0])
        for p in lines:
            totals.add(*p)
        day += timedelta(days=1)
    return totals


def s0_partial(totals, start, end):
    return (totals.first is None or (totals.first - start).total_seconds() > EDGE
            or (end - totals.last).total_seconds() > EDGE)


# --- one day ---------------------------------------------------------------------

def day_sources(day, tz, s0dirs, p1dir):
    """The log files that can hold data of a local day (existing or not)."""
    start, end = day_bounds(day, tz)
    paths = []
    d = start.date()
    while d <= end.date():
        paths += [s / d.strftime("%Y") / (d.strftime("%Y%m%d") + ".txt") for s in s0dirs]
        d += timedelta(days=1)
    hour = start
    while hour < end:
        paths.append(p1_file(p1dir, hour))
        hour += timedelta(hours=1)
    return paths


def cached_day(day, tz, s0dirs, p1dir, grid, cache_dir):
    """A completed day's figures from the cache, or computed and then cached.

    A completed day is computed once. The cached file is used when it was made
    at least SETTLE after the day ended, is newer than the day's source files
    and was made with the same house/solar channels; otherwise the day is
    computed again (e.g. after a backfill was added).

    The logger's S0 file that holds the end of the day keeps growing with the
    next day's lines (files are per UTC day), so it is left out of the check.
    """
    if cache_dir is None:
        return summarize_day(day, tz, s0dirs, p1dir, grid)
    path = cache_dir / f"{day.isoformat()}.json"
    _, end = day_bounds(day, tz)
    runs_on = s0dirs[0] / end.strftime("%Y") / (end.strftime("%Y%m%d") + ".txt")
    try:
        made = path.stat().st_mtime
        newest = max((p.stat().st_mtime for p in day_sources(day, tz, s0dirs, p1dir)
                      if p != runs_on and p.exists()), default=0)
        cached = json.loads(path.read_text())
        if made > newest and made > (end + SETTLE).timestamp() and cached.get("grid") == grid:
            return cached["summary"]
    except (OSError, ValueError, KeyError):
        pass
    summary = summarize_day(day, tz, s0dirs, p1dir, grid)
    save_day(summary, grid, cache_dir)
    return summary


def save_day(summary, grid, cache_dir):
    """Store a completed day's figures (written to a temporary file, then renamed)."""
    if cache_dir is None:
        return
    path = cache_dir / f"{summary['date']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"grid": grid, "summary": summary}))
    tmp.replace(path)


def summarize_day(day, tz, s0dirs, p1dir, grid=None):
    """Figures for one completed local day."""
    start, end = day_bounds(day, tz)
    first, fh = p1_first(p1dir, start, end)
    last, lh = p1_last(p1dir, start, end)
    s0 = s0_scan(s0dirs, start, end, S0Totals(grid))
    return {
        "date": day.isoformat(),
        "p1": {"first": first, "last": last,
               "partial": first is None or fh != start or lh != end - timedelta(hours=1)},
        "s0": {"pulses": s0.as_json(), "grid": s0.grid_json(), "partial": s0_partial(s0, start, end)},
    }
