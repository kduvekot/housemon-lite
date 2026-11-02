"""
Per-hour records: the basis for both the graph and "today".

Per UTC hour:
  minutes   60 x [house pulses, solar pulses, sum of P1 net watts, P1 telegrams]
            (for the graph);
  channels  S0 pulses per node and channel in that hour (for "today");
  gridIn, gridOut   the S0 grid estimate (house minus solar per 30 s) in pulses;
  first, last       time of the first and last S0 line in that hour.

The current hour is filled live. A completed hour is stored once as a small
JSON file (cache/hours/YYYYMMDDHH.json); at start-up only hours that are not
in the cache (normally just the current one) are built from the log files.
Dutch midnight is always on a whole UTC hour, so "today" is simply the sum of
today's hours.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import daystats

log = logging.getLogger("housemon-lite")

P1_NET = re.compile(rb"1-0:1\.7\.0\((\d+\.\d+)\*kW\).*?1-0:2\.7\.0\((\d+\.\d+)\*kW\)", re.S)
SETTLE = timedelta(minutes=10)   # an hour is final this long after it ended
FORMAT = 2                        # cache layout; older files are rebuilt


def hour_of(when):
    return when.replace(minute=0, second=0, microsecond=0)


def empty_hour():
    return {"minutes": [[0, 0, 0.0, 0] for _ in range(60)],   # house, solar, P1 W-sum, P1 count
            "channels": {}, "gridIn": 0, "gridOut": 0, "first": None, "last": None}


def p1_net(telegram):
    """Net grid power in W (taken minus delivered) from one telegram, or None."""
    m = P1_NET.search(telegram)
    return None if m is None else 1000 * (float(m.group(1)) - float(m.group(2)))


def iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S")


class HourStore:
    def __init__(self, s0dirs, p1dir, grid, cache_dir):
        self.s0dirs, self.p1dir, self.grid = s0dirs, p1dir, grid
        self.cache_dir = cache_dir / "hours" if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hours = {}         # UTC hour (datetime) -> record (see empty_hour)
        self.live = None        # the hour being filled live
        self.s0 = daystats.S0Totals(grid)   # live S0 counters
        self.s0_until = None    # S0 lines up to here are already in the hours

    # --- building hours from the logs -----------------------------------------

    def load(self, first, last):
        """Make hours first .. last (UTC, inclusive) available: cache or logs."""
        missing = []
        hour = first
        while hour <= last:
            cached = self._read_cache(hour)
            if cached is not None:
                self.hours[hour] = cached
            else:
                missing.append(hour)
            hour += timedelta(hours=1)
        if missing:
            self._build(missing[0], missing[-1], set(missing))
        return len(missing)

    def _build(self, first, last, wanted):
        built = {h: empty_hour() for h in wanted}
        # S0: one pass from a few minutes before `first` (to know the counters).
        # Lines before that are skipped on their timestamp text alone, which is
        # much cheaper than parsing them.
        totals = daystats.S0Totals(self.grid)
        start, end = first - timedelta(minutes=5), last + timedelta(hours=1)
        start_text = iso(start)
        day = start.date()
        while day <= end.date():
            lines = []
            for d in self.s0dirs:
                path = d / day.strftime("%Y") / (day.strftime("%Y%m%d") + ".txt")
                try:
                    with open(path, encoding="ascii", errors="replace") as f:
                        for line in f:
                            if line[2:21] < start_text:
                                continue
                            p = daystats.parse_s0(line)
                            if p and start <= p[0] < end:
                                lines.append(p)
                except OSError:
                    pass
            lines.sort(key=lambda p: p[0])
            for when, node, values in lines:
                self._add_s0(built, totals, when, node, values)
            day += timedelta(days=1)
        # the live part continues from these counters, after the last line counted
        self.s0 = totals
        self.s0_until = totals.last
        # P1: telegrams come once a second; a finished file that holds fewer
        # than 3600 started late, so its telegrams are placed at the end
        for hour in wanted:
            try:
                data = daystats.p1_file(self.p1dir, hour).read_bytes()
            except OSError:
                continue
            tels = [m.group(0) for m in daystats.P1_TELEGRAM.finditer(data)]
            finished = datetime.now(timezone.utc) >= hour + timedelta(hours=1)
            offset = max(0, 3600 - len(tels)) if finished else 0
            for k, tel in enumerate(tels):
                net = p1_net(tel)
                if net is not None:
                    m = built[hour]["minutes"][min(59, (offset + k) // 60)]
                    m[2] += net
                    m[3] += 1
        now = datetime.now(timezone.utc)
        for hour, rec in built.items():
            self.hours[hour] = rec
            if now >= hour + timedelta(hours=1) + SETTLE:
                self._write_cache(hour, rec)

    def _add_s0(self, hours, totals, when, node, values):
        grid_before = (totals.grid_in, totals.grid_out)
        delta = totals.add(when, node, values)
        rec = hours.get(hour_of(when))
        if delta is None or rec is None:
            return                  # stale packet, or an hour we do not keep
        stamp = iso(when)
        rec["first"] = rec["first"] or stamp
        rec["last"] = stamp
        if not delta:
            return                  # first packet: nothing to add yet
        ch = rec["channels"].setdefault(str(node), [0] * 8)
        for c, n in delta.items():
            ch[c - 1] += n
        rec["gridIn"] += totals.grid_in - grid_before[0]
        rec["gridOut"] += totals.grid_out - grid_before[1]
        if node == self.grid["node"]:
            m = rec["minutes"][when.minute]
            m[0] += sum(delta[c] for c in self.grid["house"])
            m[1] += sum(delta[c] for c in self.grid["solar"])

    # --- live --------------------------------------------------------------------

    def on_s0(self, parsed):
        when = parsed[0]
        if self.s0_until is not None and when <= self.s0_until:
            return          # already counted when the hours were built
        self._roll(when)
        self._add_s0(self.hours, self.s0, *parsed)

    def on_p1(self, telegram, when):
        self._roll(when)
        net = p1_net(telegram)
        rec = self.hours.get(hour_of(when))
        if net is not None and rec is not None:
            m = rec["minutes"][when.minute]
            m[2] += net
            m[3] += 1

    def _roll(self, when):
        """On a new UTC hour: store the finished one, start the next."""
        hour = hour_of(when)
        if hour == self.live:
            return
        if self.live is not None and self.live in self.hours:
            self._write_cache(self.live, self.hours[self.live])
        self.live = hour
        self.hours.setdefault(hour, empty_hour())
        for old in [h for h in self.hours if h < hour - timedelta(days=8)]:
            del self.hours[old]

    # --- cache -------------------------------------------------------------------

    def _read_cache(self, hour):
        if not self.cache_dir:
            return None
        try:
            c = json.loads((self.cache_dir / f"{hour:%Y%m%d%H}.json").read_text())
            if c.get("format") == FORMAT and c.get("grid") == self.grid:
                return c["hour"]
        except (OSError, ValueError, KeyError):
            pass
        return None

    def _write_cache(self, hour, rec):
        if not self.cache_dir:
            return
        path = self.cache_dir / f"{hour:%Y%m%d%H}.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"format": FORMAT, "grid": self.grid, "hour": rec}))
            tmp.replace(path)
        except OSError as e:        # a cache problem must never stop the live data
            log.warning("cannot cache hour %s: %s", f"{hour:%Y%m%d%H}", e)

    # --- sums over a range of hours (e.g. today) -------------------------------------

    def summary(self, start, end):
        """S0 pulses per node/channel, grid estimate and first/last S0 time
        for the hours start <= hour < end (UTC hour boundaries)."""
        channels, grid_in, grid_out, first, last = {}, 0, 0, None, None
        hour = start
        while hour < end:
            rec = self.hours.get(hour)
            if rec:
                for node, ch in rec["channels"].items():
                    tot = channels.setdefault(node, [0] * 8)
                    for i, n in enumerate(ch):
                        tot[i] += n
                grid_in += rec["gridIn"]
                grid_out += rec["gridOut"]
                first = first or rec["first"]
                last = rec["last"] or last
            hour += timedelta(hours=1)
        if start <= hour_of(datetime.now(timezone.utc)) < end:   # include the open 30 s step
            net = self.s0.step_net
            grid_in += max(net, 0)
            grid_out += max(-net, 0)
        return {
            "pulses": {n: {str(i + 1): p for i, p in enumerate(ch)} for n, ch in channels.items()},
            "grid": {"in": grid_in, "out": grid_out},
            "first": first, "last": last,
        }

    # --- for the page --------------------------------------------------------------

    def series(self, hours, pulse_wh):
        """Per-minute averages in W for the last `hours` hours, oldest first."""
        now = datetime.now(timezone.utc)
        start = hour_of(now) - timedelta(hours=hours - 1)
        per_min = pulse_wh * 60      # pulses in one minute -> average W
        out = {"start": start.isoformat(), "step": 60, "house": [], "solar": [], "grid": [], "gridEstimate": []}
        hour = start
        while hour <= now:
            rec = self.hours.get(hour)
            for i in range(60):
                t = hour + timedelta(minutes=i)
                if t + timedelta(minutes=1) > now:
                    break           # the running minute is not complete yet
                m = rec["minutes"][i] if rec else None
                # the house never uses nothing for a whole minute: no pulses = no S0 data
                has_s0 = m is not None and (m[0] or m[1])
                house = round(m[0] * per_min) if has_s0 else None
                solar = round(m[1] * per_min) if has_s0 else None
                grid = round(m[2] / m[3]) if m and m[3] else None
                out["house"].append(house)
                out["solar"].append(solar)
                out["grid"].append(grid)
                out["gridEstimate"].append(house - solar if grid is None and has_s0 else None)
            hour += timedelta(hours=1)
        return out
