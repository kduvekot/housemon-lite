#!/usr/bin/env python3
"""
housemon-lite: a small live view of the S0 (RF12) and P1 smart-meter data.

The server does as little as possible, so it runs comfortably on a Pi 1:

  * every second it checks whether the raw log files written by
    jeenode-listener (S0 over RF12) and p1-logger (P1 telegrams) have grown;
  * it keeps the newest S0 line per RF12 node and the newest complete P1
    telegram, unchanged, and pushes them to the browsers over Server-Sent
    Events (/events);
  * per local day (daystats.py) it keeps the P1 meter readings at midnight,
    the S0 pulses so far, and the figures of the last 7 completed days.

Decoding, the P1 CRC check of live telegrams and the display all happen in
the browser (web/app.js). Only the Python standard library is used.
"""

import argparse
import functools
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import daystats
import series

POLL_INTERVAL = 1.0     # seconds between checks of the log files
BACKLOG = 4096          # bytes read back from a file at start-up
KEEPALIVE = 15.0        # seconds between SSE keep-alive comments
HISTORY_DAYS = 7        # completed days shown
GRAPH_HOURS = 36        # hours of per-minute data kept ready for the graph

DEFAULT_S0_DIR = "/var/lib/housemon/logger"   # YYYY/YYYYMMDD.txt (UTC)
DEFAULT_P1_DIR = "/var/lib/p1-logger/data"    # YYYY/YYYYMMDD/HH.txt (UTC)
DEFAULT_TZ = "Europe/Amsterdam"
# Optional extra S0 data in the same layout (e.g. a temporary backfill from
# another receiver); read only if the directory exists.
DEFAULT_S0_EXTRA = "/var/lib/housemon-lite-backfill/s0"

S0_LINE = re.compile(r"^L (\S+) \S+ (OK (\d+)(?: \d+)*)\s*$")
P1_TELEGRAM = re.compile(rb"/[^!]*![0-9A-Fa-f]{4}\r?\n")

log = logging.getLogger("housemon-lite")


# ---------------------------------------------------------------------------
# Following log files whose name depends on the current (UTC) time
# ---------------------------------------------------------------------------

def s0_path(base):
    return lambda now: base / now.strftime("%Y") / (now.strftime("%Y%m%d") + ".txt")


def p1_path(base):
    return lambda now: base / now.strftime("%Y") / now.strftime("%Y%m%d") / (now.strftime("%H") + ".txt")


class Follower:
    """Returns the bytes appended to a rotating log file since the last call."""

    def __init__(self, path_for):
        self.path_for = path_for
        self.path = None
        self.pos = 0

    def poll(self):
        new = self.path_for(datetime.now(timezone.utc))
        data = b""
        if self.path is None:
            if new.exists():
                # first time: start a little before the end, to show data at once
                self.path = new
                self.pos = max(0, new.stat().st_size - BACKLOG)
        elif new != self.path and new.exists():
            # the logger moved on to a new file: finish the old one first
            data += self._read()
            self.path, self.pos = new, 0
        if self.path is not None:
            data += self._read()
        return data

    def _read(self):
        try:
            with open(self.path, "rb") as f:
                size = f.seek(0, 2)
                if size < self.pos:      # file was replaced or truncated
                    self.pos = 0
                f.seek(self.pos)
                data = f.read()
        except OSError as e:
            log.debug("cannot read %s: %s", self.path, e)
            return b""
        self.pos += len(data)
        return data


# ---------------------------------------------------------------------------
# Latest values, shared between the polling thread and the browser connections
# ---------------------------------------------------------------------------

class Hub:
    """Holds the newest item per key; wakes up waiting connections on change."""

    def __init__(self):
        self.cond = threading.Condition()
        self.items = {}         # key -> (seq, payload)
        self.seq = 0

    def publish(self, key, payload):
        with self.cond:
            self.seq += 1
            self.items[key] = (self.seq, payload)
            self.cond.notify_all()

    def wait_newer(self, seen, timeout):
        """Items with a higher seq than in `seen`; waits up to `timeout`."""
        def newer():
            return {k: v for k, v in self.items.items() if v[0] > seen.get(k, 0)}
        with self.cond:
            self.cond.wait_for(lambda: newer(), timeout)
            return newer()


# ---------------------------------------------------------------------------
# Today and the last days (local days; see daystats.py)
# ---------------------------------------------------------------------------

class DayStore:
    """Today and the last completed days. Today's S0 figures come from the hour
    records (series.py): Dutch midnight is on a whole UTC hour, so today is the
    sum of today's hours. The P1 figures need only the meter reading at midnight."""

    def __init__(self, hub, tz, s0dirs, p1dir, grid, cache_dir, hours):
        self.hub, self.tz, self.s0dirs, self.p1dir, self.grid = hub, tz, s0dirs, p1dir, grid
        self.cache_dir = cache_dir      # completed days as small JSON files (None: no cache)
        self.hours = hours
        self.lock = threading.Lock()
        self.history = []           # completed days, newest first
        self.start_today(daystats.local_today(tz))

    def start_today(self, day):
        self.today = day
        self.start, self.end = daystats.day_bounds(day, self.tz)
        self.p1_first = None
        self.p1_first_hour = None

    def load_today(self):
        """At start-up: the P1 meter reading at midnight (the S0 figures are in the hours)."""
        now = datetime.now(timezone.utc)
        self.p1_first, self.p1_first_hour = daystats.p1_first(self.p1dir, self.start, now + timedelta(hours=1))
        self.publish()

    def load_history(self):
        """Background thread at start-up: the last completed days, newest first.
        Days already in the cache are only read; the others are computed once."""
        for back in range(1, HISTORY_DAYS + 1):
            day = self.today - timedelta(days=back)
            summary = daystats.cached_day(day, self.tz, self.s0dirs, self.p1dir, self.grid, self.cache_dir)
            with self.lock:
                self.history.append(summary)
        log.info("history ready: %s .. %s", self.history[-1]["date"], self.history[0]["date"])
        self.publish()

    def on_p1(self, telegram):
        """Every new P1 telegram: only needed until today's first reading is known."""
        if self.p1_first is None:
            self.p1_first = daystats.p1_readings(telegram)
            self.p1_first_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            if self.p1_first:
                self.publish()

    def s0_today(self):
        """Today's S0 figures from the hour records, with the 'incomplete' flag."""
        s = self.hours.summary(self.start, self.end)
        first = datetime.fromisoformat(s["first"]).replace(tzinfo=timezone.utc) if s["first"] else None
        s["partial"] = first is None or (first - self.start).total_seconds() > daystats.EDGE
        return s

    def check_midnight(self):
        day = daystats.local_today(self.tz)
        if day == self.today:
            return
        last, last_hour = daystats.p1_last(self.p1dir, self.start, self.end)
        s0 = self.hours.summary(self.start, self.end)
        first = datetime.fromisoformat(s0["first"]).replace(tzinfo=timezone.utc) if s0["first"] else None
        lastline = datetime.fromisoformat(s0["last"]).replace(tzinfo=timezone.utc) if s0["last"] else None
        done = {
            "date": self.today.isoformat(),
            "p1": {"first": self.p1_first, "last": last,
                   "partial": self.p1_first is None or self.p1_first_hour != self.start
                              or last_hour != self.end - timedelta(hours=1)},
            "s0": {"pulses": s0["pulses"], "grid": s0["grid"],
                   "partial": first is None or (first - self.start).total_seconds() > daystats.EDGE
                              or (self.end - lastline).total_seconds() > daystats.EDGE},
        }
        with self.lock:
            self.history = ([done] + self.history)[:HISTORY_DAYS]
        try:
            daystats.save_day(done, self.grid, self.cache_dir)
        except OSError as e:
            log.warning("cannot cache %s: %s", done["date"], e)
        self.start_today(day)
        log.info("new day %s", day)
        self.publish()

    def publish(self):
        with self.lock:
            history = list(self.history)
        self.hub.publish("days", {
            "tz": str(self.tz),
            "today": {"date": self.today.isoformat(), "p1First": self.p1_first,
                      "p1Partial": self.p1_first_hour != self.start,
                      "s0Partial": self.s0_today()["partial"]},
            "history": history,
        })


def poll_loop(hub, s0, p1, days, hours):
    # the hours first: today's S0 figures are made from them
    now = series.hour_of(datetime.now(timezone.utc))
    built = hours.load(now - timedelta(hours=GRAPH_HOURS - 1), now)
    log.info("hours ready (%d built from the logs, the rest from the cache)", built)
    days.load_today()
    threading.Thread(target=days.load_history, daemon=True).start()
    s0_buf = ""
    p1_buf = b""
    while True:
        try:
            s0_buf, p1_buf = poll_once(hub, s0, p1, days, hours, s0_buf, p1_buf)
        except Exception:           # log and go on: the live data must keep flowing
            log.exception("error while reading the logs; continuing")
        time.sleep(POLL_INTERVAL)


def poll_once(hub, s0, p1, days, hours, s0_buf, p1_buf):
    """One round: read what the loggers added, update the figures, publish."""
    days.check_midnight()

    # S0: complete lines; count them all, show the newest one per node
    s0_buf += s0.poll().decode("ascii", "replace")
    *lines, s0_buf = s0_buf.split("\n")
    newest = {}
    for line in lines:
        m = S0_LINE.match(line)
        if m:
            newest[int(m.group(3)) & 0x1F] = {"time": m.group(1), "line": m.group(2)}
            parsed = daystats.parse_s0(line)
            if parsed:
                hours.on_s0(parsed)
    if newest:
        today = days.s0_today()
        for node, payload in newest.items():
            payload["today"] = today["pulses"].get(str(node), {})
            payload["todayGrid"] = today["grid"]
            payload["lastPulse"] = hours.last_pulse(node)
            hub.publish(f"s0-{node}", payload)

    # P1: complete telegrams (/ ... !CRC); show the newest one
    p1_buf += p1.poll()
    found = list(P1_TELEGRAM.finditer(p1_buf))
    if found:
        seen = datetime.now(timezone.utc)
        for m in found:
            hours.on_p1(m.group(0), seen)
        newest_tel = found[-1].group(0)
        days.on_p1(newest_tel)
        hub.publish("p1", {"seen": time.time(), "telegram": newest_tel.decode("ascii", "replace")})
        p1_buf = p1_buf[found[-1].end():]
    if len(p1_buf) > 65536:          # never let garbage grow without bound
        p1_buf = p1_buf[-8192:]
    return s0_buf, p1_buf


# ---------------------------------------------------------------------------
# HTTP: static files from web/, plus /events (SSE) and /api/latest (JSON)
# ---------------------------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    hub = None
    hours = None
    pulse_wh = 0.5

    def do_GET(self):
        if self.path == "/events":
            self.send_events()
        elif self.path.startswith("/api/series"):
            query = parse_qs(urlsplit(self.path).query)
            try:
                n = max(1, min(168, int(query.get("hours", ["36"])[0])))
            except ValueError:
                n = 36
            self.send_json(self.hours.series(n, self.pulse_wh))
        elif self.path == "/api/latest":
            self.send_json({k: v for k, (_, v) in self.hub.items.items()})
        else:
            super().do_GET()

    def send_json(self, data):
        body = json.dumps(data, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        seen = {}
        try:
            while True:
                items = self.hub.wait_newer(seen, KEEPALIVE)
                if not items:
                    self.wfile.write(b": ping\n\n")
                for key, (seq, payload) in items.items():
                    kind = "s0" if key.startswith("s0-") else key
                    self.wfile.write(f"event: {kind}\ndata: {json.dumps(payload)}\n\n".encode())
                    seen[key] = seq
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass        # browser went away

    def end_headers(self):
        # Browsers may keep a copy of the page files, but must ask first whether
        # it changed (answered with a short "304 Not Modified" when it did not),
        # so an update shows up at the next reload.
        if not self.path.startswith(("/events", "/api/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)


def main(argv=None):
    p = argparse.ArgumentParser(description="small live view of S0 and P1 data")
    p.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: all)")
    p.add_argument("--port", type=int, default=3333, help="TCP port (default: 3333)")
    p.add_argument("--s0-dir", default=DEFAULT_S0_DIR, help=f"jeenode-listener logs (default: {DEFAULT_S0_DIR})")
    p.add_argument("--p1-dir", default=DEFAULT_P1_DIR, help=f"p1-logger data (default: {DEFAULT_P1_DIR})")
    p.add_argument("--tz", default=DEFAULT_TZ, help=f"time zone of the day boundary (default: {DEFAULT_TZ})")
    p.add_argument("--s0-extra-dir", action="append", default=None,
                   help=f"extra S0 data, same layout; repeatable (default: {DEFAULT_S0_EXTRA} if it exists)")
    p.add_argument("--state-dir", default=os.environ.get("STATE_DIRECTORY"),
                   help="where completed days are cached (default: $STATE_DIRECTORY from systemd; none: no cache)")
    p.add_argument("--web-dir", default=str(Path(__file__).resolve().parent / "web"), help="static files")
    p.add_argument("--debug", action="store_true", help="log every request")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Which S0 channels are house and solar, for the grid estimate of days
    # without P1 data; the page's own config.json says so.
    cfg = json.loads((Path(args.web_dir) / "config.json").read_text())["s0"]
    grid = {"node": cfg["node"],
            "house": [int(c) for c, i in cfg["channels"].items() if i.get("kind") == "house"],
            "solar": [int(c) for c, i in cfg["channels"].items() if i.get("kind") == "solar"]}
    extra = args.s0_extra_dir if args.s0_extra_dir is not None else [DEFAULT_S0_EXTRA]
    s0dirs = [Path(args.s0_dir)] + [Path(d) for d in extra if Path(d).is_dir()]

    hub = Hub()
    cache_dir = Path(args.state_dir) / "days" if args.state_dir else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    hours = series.HourStore(s0dirs, Path(args.p1_dir), grid, Path(args.state_dir) if args.state_dir else None)
    days = DayStore(hub, ZoneInfo(args.tz), s0dirs, Path(args.p1_dir), grid, cache_dir, hours)
    s0 = Follower(s0_path(Path(args.s0_dir)))
    p1 = Follower(p1_path(Path(args.p1_dir)))
    threading.Thread(target=poll_loop, args=(hub, s0, p1, days, hours), daemon=True).start()

    Handler.hub = hub
    Handler.hours = hours
    Handler.pulse_wh = cfg.get("pulseWh", 0.5)
    server = ThreadingHTTPServer((args.bind, args.port),
                                 functools.partial(Handler, directory=args.web_dir))
    server.daemon_threads = True
    log.info("serving %s on %s:%d (S0 %s, P1 %s, days in %s, cache %s)",
             args.web_dir, args.bind, args.port, " + ".join(map(str, s0dirs)), args.p1_dir, args.tz, cache_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
