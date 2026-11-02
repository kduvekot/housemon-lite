#!/usr/bin/env python3
"""
housemon-lite: a small live view of the S0 (RF12) and P1 smart-meter data.

The server does as little as possible, so it runs comfortably on a Pi 1:

  * every second it checks whether the raw log files written by
    jeenode-listener (S0 over RF12) and p1-logger (P1 telegrams) have grown;
  * it keeps the newest S0 line per RF12 node and the newest complete P1
    telegram, unchanged;
  * it pushes those to the browsers over Server-Sent Events (/events).

Decoding, the P1 CRC check and the display all happen in the browser
(web/app.js). Only the Python standard library is used.
"""

import argparse
import functools
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

POLL_INTERVAL = 1.0     # seconds between checks of the log files
BACKLOG = 4096          # bytes read back from a file at start-up
KEEPALIVE = 15.0        # seconds between SSE keep-alive comments

DEFAULT_S0_DIR = "/var/lib/housemon/logger"   # YYYY/YYYYMMDD.txt (UTC)
DEFAULT_P1_DIR = "/var/lib/p1-logger/data"    # YYYY/YYYYMMDD/HH.txt (UTC)

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


def poll_loop(hub, s0, p1):
    s0_buf = ""
    p1_buf = b""
    while True:
        # S0: complete lines; keep the newest one per RF12 node
        s0_buf += s0.poll().decode("ascii", "replace")
        *lines, s0_buf = s0_buf.split("\n")
        newest = {}
        for line in lines:
            m = S0_LINE.match(line)
            if m:
                newest[int(m.group(3)) & 0x1F] = {"time": m.group(1), "line": m.group(2)}
        for node, payload in newest.items():
            hub.publish(f"s0-{node}", payload)

        # P1: complete telegrams (/ ... !CRC); keep the newest one
        p1_buf += p1.poll()
        found = list(P1_TELEGRAM.finditer(p1_buf))
        if found:
            hub.publish("p1", {"seen": time.time(),
                               "telegram": found[-1].group(0).decode("ascii", "replace")})
            p1_buf = p1_buf[found[-1].end():]
        if len(p1_buf) > 65536:          # never let garbage grow without bound
            p1_buf = p1_buf[-8192:]

        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# HTTP: static files from web/, plus /events (SSE) and /api/latest (JSON)
# ---------------------------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    hub = None

    def do_GET(self):
        if self.path == "/events":
            self.send_events()
        elif self.path == "/api/latest":
            body = json.dumps({k: v for k, (_, v) in self.hub.items.items()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

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

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)


def main(argv=None):
    p = argparse.ArgumentParser(description="small live view of S0 and P1 data")
    p.add_argument("--bind", default="0.0.0.0", help="address to listen on (default: all)")
    p.add_argument("--port", type=int, default=3333, help="TCP port (default: 3333)")
    p.add_argument("--s0-dir", default=DEFAULT_S0_DIR, help=f"jeenode-listener logs (default: {DEFAULT_S0_DIR})")
    p.add_argument("--p1-dir", default=DEFAULT_P1_DIR, help=f"p1-logger data (default: {DEFAULT_P1_DIR})")
    p.add_argument("--web-dir", default=str(Path(__file__).resolve().parent / "web"), help="static files")
    p.add_argument("--debug", action="store_true", help="log every request")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    hub = Hub()
    s0 = Follower(s0_path(Path(args.s0_dir)))
    p1 = Follower(p1_path(Path(args.p1_dir)))
    threading.Thread(target=poll_loop, args=(hub, s0, p1), daemon=True).start()

    Handler.hub = hub
    server = ThreadingHTTPServer((args.bind, args.port),
                                 functools.partial(Handler, directory=args.web_dir))
    server.daemon_threads = True
    log.info("serving %s on %s:%d (S0 %s, P1 %s)", args.web_dir, args.bind, args.port, args.s0_dir, args.p1_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
