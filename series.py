"""
Per-minute figures for the graph: house and solar from S0, grid from P1
(estimated from S0, house minus solar, where there is no P1).

Kept per UTC hour as 60 minutes of [house pulses, solar pulses, sum of P1 net
watts, number of P1 telegrams]. The current hour is filled live by the
server; a completed hour is stored once as a small JSON file
(cache/hours/YYYYMMDDHH.json). Hours that are missing (e.g. after the
service was down) are built from the log files: S0 in one pass over the
range, P1 one hourly file at a time.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import daystats

log = logging.getLogger("housemon-lite")

P1_NET = re.compile(rb"1-0:1\.7\.0\((\d+\.\d+)\*kW\).*?1-0:2\.7\.0\((\d+\.\d+)\*kW\)", re.S)
SETTLE = timedelta(minutes=10)   # an hour is final this long after it ended


def hour_of(when):
    return when.replace(minute=0, second=0, microsecond=0)


def empty_hour():
    return [[0, 0, 0.0, 0] for _ in range(60)]     # house, solar, P1 W-sum, P1 count


def p1_net(telegram):
    """Net grid power in W (taken minus delivered) from one telegram, or None."""
    m = P1_NET.search(telegram)
    return None if m is None else 1000 * (float(m.group(1)) - float(m.group(2)))


class HourStore:
    def __init__(self, s0dirs, p1dir, grid, cache_dir):
        self.s0dirs, self.p1dir, self.grid = s0dirs, p1dir, grid
        self.cache_dir = cache_dir / "hours" if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hours = {}         # UTC hour (datetime) -> 60 minutes
        self.live = None        # the hour being filled live
        self.s0 = daystats.S0Totals()   # live S0 counters (for the pulses per line)
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
        # S0: one pass from a few minutes before `first` (to know the counters)
        totals = daystats.S0Totals()
        start, end = first - timedelta(minutes=5), last + timedelta(hours=1)
        day = start.date()
        while day <= end.date():
            lines = []
            for d in self.s0dirs:
                path = d / day.strftime("%Y") / (day.strftime("%Y%m%d") + ".txt")
                try:
                    with open(path, encoding="ascii", errors="replace") as f:
                        lines += [p for p in map(daystats.parse_s0, f) if p and start <= p[0] < end]
                except OSError:
                    pass
            lines.sort(key=lambda p: p[0])
            for when, node, values in lines:
                self._add_s0(built, totals, when, node, values)
            day += timedelta(days=1)
        # the live part continues from these counters, after the last line counted
        self.s0 = totals
        self.s0_until = totals.last
        # P1: telegrams come once a second; a file that holds fewer than 3600
        # started late, so its telegrams are placed at the end of the hour
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
                    m = built[hour][min(59, (offset + k) // 60)]
                    m[2] += net
                    m[3] += 1
        now = datetime.now(timezone.utc)
        for hour, minutes in built.items():
            self.hours[hour] = minutes
            if now >= hour + timedelta(hours=1) + SETTLE:
                self._write_cache(hour, minutes)

    def _add_s0(self, hours, totals, when, node, values):
        delta = totals.add(when, node, values)
        if not delta or node != self.grid["node"]:
            return
        minutes = hours.get(hour_of(when))
        if minutes is None:
            return
        m = minutes[when.minute]
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
        minutes = self.hours.get(hour_of(when))
        if net is not None and minutes is not None:
            m = minutes[when.minute]
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
            return c["minutes"] if c.get("grid") == self.grid else None
        except (OSError, ValueError, KeyError):
            return None

    def _write_cache(self, hour, minutes):
        if not self.cache_dir:
            return
        path = self.cache_dir / f"{hour:%Y%m%d%H}.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"grid": self.grid, "minutes": minutes}))
            tmp.replace(path)
        except OSError as e:        # a cache problem must never stop the live data
            log.warning("cannot cache hour %s: %s", f"{hour:%Y%m%d%H}", e)

    # --- for the page --------------------------------------------------------------

    def series(self, hours, pulse_wh):
        """Per-minute averages in W for the last `hours` hours, oldest first."""
        now = datetime.now(timezone.utc)
        start = hour_of(now) - timedelta(hours=hours - 1)
        per_min = pulse_wh * 60      # pulses in one minute -> average W
        out = {"start": start.isoformat(), "step": 60, "house": [], "solar": [], "grid": [], "gridEstimate": []}
        hour = start
        while hour <= now:
            minutes = self.hours.get(hour)
            for i in range(60):
                t = hour + timedelta(minutes=i)
                if t + timedelta(minutes=1) > now:
                    break           # the running minute is not complete yet
                m = minutes[i] if minutes else None
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
